"""Scan every AWS region for resources that are quietly costing money.

Each finding says what it is, where it is, what it costs per month, and *why*
it looks forgotten, so the owner can decide in seconds.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from botocore.exceptions import BotoCoreError, ClientError

from . import pricing

IDLE_CPU_PERCENT = 2.0
FORGOTTEN_AFTER_DAYS = 2


def _now():
    return datetime.now(timezone.utc)


def _name(tags):
    for t in tags or []:
        if t.get("Key") == "Name":
            return t.get("Value")
    return ""


def _tag(tags, key):
    for t in tags or []:
        if t.get("Key") == key:
            return t.get("Value")
    return None


def _age_days(launched, now, tags=None, demo=False):
    # Demo accounts can't backdate resources, so they carry their age in a tag.
    override = _tag(tags, "billbomb:demo-age-days") if demo else None
    if override is not None:
        return float(override)
    if not launched:
        return None
    return round((now - launched).total_seconds() / 86400, 1)


def _finding(kind, rid, region, monthly_cost, *, name="", state="", age=None, reasons=(),
             action=None, detail="", usage=""):
    zombie = bool(reasons)
    return {
        "kind": kind, "id": rid, "region": region, "name": name, "state": state,
        "monthly_cost": round(monthly_cost, 2), "age_days": age, "reasons": list(reasons),
        "zombie": zombie, "action": action, "detail": detail, "usage": usage,
    }


def _metric(cw, namespace, metric, dims, now, stat="Average", days=7):
    """7-day average (for Average) or total (for Sum) of hourly datapoints; None if there are none."""
    try:
        resp = cw.get_metric_statistics(
            Namespace=namespace, MetricName=metric, Dimensions=dims,
            StartTime=now - timedelta(days=days), EndTime=now, Period=3600, Statistics=[stat])
    except (ClientError, BotoCoreError):
        return None
    points = [p[stat] for p in resp.get("Datapoints", [])]
    if not points:
        return None
    return round(sum(points) / len(points), 2) if stat == "Average" else round(sum(points), 2)


# ------------------------------------------------------------------- checks

def scan_ec2(session, region, now, demo=False):
    ec2 = session.client("ec2", region_name=region)
    cw = session.client("cloudwatch", region_name=region)
    out = []

    attached_volumes = {}
    for page in ec2.get_paginator("describe_instances").paginate():
        for res in page["Reservations"]:
            for inst in res["Instances"]:
                state = inst["State"]["Name"]
                if state in ("terminated", "shutting-down"):
                    continue
                itype, iid, tags = inst["InstanceType"], inst["InstanceId"], inst.get("Tags")
                age = _age_days(inst.get("LaunchTime"), now, tags, demo)
                for m in inst.get("BlockDeviceMappings", []):
                    if "Ebs" in m:
                        attached_volumes[m["Ebs"]["VolumeId"]] = iid
                if state != "running":
                    continue
                cost = pricing.ec2_monthly(itype)
                if inst.get("PublicIpAddress"):
                    cost += pricing.monthly(pricing.PUBLIC_IPV4_HOURLY)
                cpu = _metric(cw, "AWS/EC2", "CPUUtilization",
                                  [{"Name": "InstanceId", "Value": iid}], now)
                reasons = []
                if cpu is not None and cpu < IDLE_CPU_PERCENT and (age or 0) >= FORGOTTEN_AFTER_DAYS:
                    reasons.append(f"Average CPU {cpu}% over the last 7 days")
                if pricing.is_gpu(itype) and (cpu is None or cpu < 10):
                    reasons.append(f"GPU instance ({itype}) with almost no load")
                usage = f"CPU {cpu}% avg" if cpu is not None else "no CPU data"
                out.append(_finding("EC2 instance", iid, region, cost, name=_name(tags), state=state,
                                    age=age, reasons=reasons, action="stop_instance", detail=itype, usage=usage))

    for page in ec2.get_paginator("describe_volumes").paginate():
        for vol in page["Volumes"]:
            if vol["State"] != "available":
                continue
            vtype, size, tags = vol.get("VolumeType", "gp2"), vol["Size"], vol.get("Tags")
            cost = size * pricing.EBS_GB_MONTH.get(vtype, 0.1)
            age = _age_days(vol.get("CreateTime"), now, tags, demo)
            out.append(_finding("EBS volume", vol["VolumeId"], region, cost, name=_name(tags),
                                state="unattached", age=age, action="delete_volume",
                                reasons=["Not attached to any instance"],
                                detail=f"{size} GB {vtype}", usage="unattached"))

    for addr in ec2.describe_addresses().get("Addresses", []):
        if addr.get("AssociationId") or addr.get("InstanceId"):
            continue
        out.append(_finding("Elastic IP", addr.get("AllocationId", addr.get("PublicIp")), region,
                            pricing.monthly(pricing.PUBLIC_IPV4_HOURLY),
                            name=_name(addr.get("Tags")) or addr.get("PublicIp", ""),
                            state="unassociated", action="release_address",
                            reasons=["Not associated with any instance"],
                            detail=addr.get("PublicIp", ""), usage="unassociated"))

    for page in ec2.get_paginator("describe_nat_gateways").paginate():
        for nat in page["NatGateways"]:
            if nat["State"] not in ("available", "pending"):
                continue
            tags, nid = nat.get("Tags"), nat["NatGatewayId"]
            age = _age_days(nat.get("CreateTime"), now, tags, demo)
            sent = _metric(cw, "AWS/NATGateway", "BytesOutToDestination",
                           [{"Name": "NatGatewayId", "Value": nid}], now, stat="Sum")
            reasons = []
            if (sent or 0) < 1_000_000 and (age or 0) >= FORGOTTEN_AFTER_DAYS:
                reasons.append("Under 1 MB of traffic in 7 days, still billed hourly")
            traffic = "no traffic data" if sent is None else f"{sent / 1e9:.2f} GB out (7d)"
            out.append(_finding("NAT gateway", nid, region, pricing.monthly(pricing.NAT_GATEWAY_HOURLY),
                                name=_name(tags), state=nat["State"], age=age, reasons=reasons,
                                action="delete_nat_gateway",
                                detail=nat.get("VpcId", ""), usage=traffic))

    for page in ec2.get_paginator("describe_snapshots").paginate(OwnerIds=["self"]):
        for snap in page["Snapshots"]:
            age = _age_days(snap.get("StartTime"), now, snap.get("Tags"), demo)
            if age is not None and age > 90:
                size = snap.get("VolumeSize", 0)
                out.append(_finding("EBS snapshot", snap["SnapshotId"], region,
                                    size * pricing.SNAPSHOT_GB_MONTH, name=_name(snap.get("Tags")),
                                    state="completed", age=age, action="delete_snapshot",
                                    reasons=[f"Snapshot is {int(age)} days old"], detail=f"{size} GB", usage="-"))
    return out


def scan_rds(session, region, now, demo=False):
    rds = session.client("rds", region_name=region)
    cw = session.client("cloudwatch", region_name=region)
    out = []
    for page in rds.get_paginator("describe_db_instances").paginate():
        for db in page["DBInstances"]:
            if db["DBInstanceStatus"] != "available":
                continue
            dbid, cls = db["DBInstanceIdentifier"], db["DBInstanceClass"]
            tags = db.get("TagList")
            age = _age_days(db.get("InstanceCreateTime"), now, tags, demo)
            cost = pricing.rds_monthly(cls) + db.get("AllocatedStorage", 20) * 0.115
            dims = [{"Name": "DBInstanceIdentifier", "Value": dbid}]
            conns = _metric(cw, "AWS/RDS", "DatabaseConnections", dims, now)
            reasons = []
            if conns is not None and conns < 0.1 and (age or 0) >= FORGOTTEN_AFTER_DAYS:
                reasons.append("No connections in the last 7 days")
            out.append(_finding("RDS database", dbid, region, cost, name=dbid, state="available", age=age,
                                reasons=reasons, action="stop_db",
                                detail=f"{cls} · {db.get('Engine', '')}",
                                usage=f"{conns:g} connections avg" if conns is not None else "no data"))
    return out


def scan_elb(session, region, now, demo=False):
    elb = session.client("elbv2", region_name=region)
    out = []
    for page in elb.get_paginator("describe_load_balancers").paginate():
        for lb in page["LoadBalancers"]:
            arn = lb["LoadBalancerArn"]
            try:
                groups = elb.describe_target_groups(LoadBalancerArn=arn).get("TargetGroups", [])
            except ClientError as e:
                if e.response["Error"]["Code"] != "TargetGroupNotFound":
                    raise
                groups = []
            targets = 0
            for g in groups:
                targets += len(elb.describe_target_health(TargetGroupArn=g["TargetGroupArn"])
                               .get("TargetHealthDescriptions", []))
            reasons = [] if targets else ["No registered targets"]
            age = _age_days(lb.get("CreatedTime"), now, None, demo)
            out.append(_finding("Load balancer", lb["LoadBalancerName"], region,
                                pricing.monthly(pricing.LOAD_BALANCER_HOURLY), name=lb["LoadBalancerName"],
                                state=lb.get("State", {}).get("Code", ""), age=age, reasons=reasons,
                                action="delete_load_balancer", detail=lb.get("Type", ""), usage=f"{targets} target(s)"))
    return out


CHECKS = (scan_ec2, scan_rds, scan_elb)


def regions(session):
    ec2 = session.client("ec2", region_name="us-east-1")
    return sorted(r["RegionName"] for r in ec2.describe_regions()["Regions"])


def scan(session, region_list=None, demo=False, workers=8):
    """Scan all regions in parallel. Returns findings plus per-region errors."""
    now = _now()
    region_list = region_list or regions(session)
    findings, errors = [], []

    def one(region):
        found, errs = [], []
        for check in CHECKS:
            try:
                found += check(session, region, now, demo)
            except (ClientError, BotoCoreError) as e:
                errs.append(f"{region}: {check.__name__}: {e}")
        return found, errs

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for found, errs in pool.map(one, region_list):
            findings += found
            errors += errs
    findings.sort(key=lambda f: (not f["zombie"], -f["monthly_cost"]))
    return {"findings": findings, "errors": errors, "regions": region_list, "scanned_at": now.isoformat()}


def summarize(findings, budget_usd=0.0, pkr_rate=280.0):
    """Monthly burn, how much is wasted, and when the budget blows up."""
    total = sum(f["monthly_cost"] for f in findings)
    wasted = sum(f["monthly_cost"] for f in findings if f["zombie"])
    daily = total / 30
    if daily <= 0:
        days_to_budget = None
    elif budget_usd <= 0:
        days_to_budget = 0
    else:
        days_to_budget = round(budget_usd / daily, 1)
    regions_hit = sorted({f["region"] for f in findings})
    return {
        "monthly_usd": round(total, 2), "monthly_pkr": round(total * pkr_rate),
        "wasted_usd": round(wasted, 2), "wasted_pkr": round(wasted * pkr_rate),
        "daily_usd": round(daily, 2), "yearly_pkr": round(total * 12 * pkr_rate),
        "days_to_budget": days_to_budget, "budget_usd": budget_usd,
        "zombies": sum(f["zombie"] for f in findings), "resources": len(findings),
        "regions": regions_hit, "pkr_rate": pkr_rate,
    }
