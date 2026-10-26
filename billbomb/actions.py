"""Defuse: stop or delete a forgotten resource. Every call is explicit and per-resource."""

from botocore.exceptions import BotoCoreError, ClientError

LABELS = {
    "stop_instance": "Stop server",
    "delete_volume": "Delete disk",
    "release_address": "Release IP",
    "delete_nat_gateway": "Delete NAT gateway",
    "delete_snapshot": "Delete snapshot",
    "stop_db": "Stop database",
    "delete_load_balancer": "Delete load balancer",
}


def defuse(session, finding):
    """Run the finding's action. Returns (ok, message)."""
    action, rid, region = finding["action"], finding["id"], finding["region"]
    ec2 = session.client("ec2", region_name=region)
    try:
        if action == "stop_instance":
            ec2.stop_instances(InstanceIds=[rid])
        elif action == "delete_volume":
            ec2.delete_volume(VolumeId=rid)
        elif action == "release_address":
            ec2.release_address(AllocationId=rid)
        elif action == "delete_nat_gateway":
            ec2.delete_nat_gateway(NatGatewayId=rid)
        elif action == "delete_snapshot":
            ec2.delete_snapshot(SnapshotId=rid)
        elif action == "stop_db":
            session.client("rds", region_name=region).stop_db_instance(DBInstanceIdentifier=rid)
        elif action == "delete_load_balancer":
            elb = session.client("elbv2", region_name=region)
            arn = elb.describe_load_balancers(Names=[rid])["LoadBalancers"][0]["LoadBalancerArn"]
            elb.delete_load_balancer(LoadBalancerArn=arn)
        else:
            return False, f"No defuse action for {finding['kind']}"
    except (ClientError, BotoCoreError) as e:
        return False, f"{LABELS.get(action, action)} failed: {e}"
    return True, f"{LABELS[action]}: {rid} ({region})"
