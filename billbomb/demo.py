"""A simulated AWS account (via moto) full of realistic forgotten resources.

The real scanner and defuse code run against it unchanged, so the demo works
without an AWS account or any risk of real charges.
"""

from datetime import datetime, timedelta, timezone

import boto3
from moto import mock_aws

_mock = None


def _age(days):
    return {"Key": "billbomb:demo-age-days", "Value": str(days)}


def _tags(name, days, resource):
    return [{"ResourceType": resource, "Tags": [{"Key": "Name", "Value": name}, _age(days)]}]


def _cpu(cw, namespace, metric, dim_name, dim_value, value):
    now = datetime.now(timezone.utc)
    cw.put_metric_data(Namespace=namespace, MetricData=[
        {"MetricName": metric, "Dimensions": [{"Name": dim_name, "Value": dim_value}],
         "Timestamp": now - timedelta(hours=h), "Value": value, "Unit": "Percent" if metric == "CPUUtilization" else "Count"}
        for h in range(1, 168, 6)])


def _instance(session, region, itype, name, days, cpu):
    ec2 = session.client("ec2", region_name=region)
    ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
    iid = ec2.run_instances(ImageId=ami, InstanceType=itype, MinCount=1, MaxCount=1,
                            TagSpecifications=_tags(name, days, "instance"))["Instances"][0]["InstanceId"]
    _cpu(session.client("cloudwatch", region_name=region), "AWS/EC2", "CPUUtilization", "InstanceId", iid, cpu)
    return iid


def seed(session):
    # us-east-1: the portfolio site that's actually in use, plus things left behind from a final-year project
    _instance(session, "us-east-1", "t3.micro", "my-portfolio-site", 120, 34.0)
    ec2 = session.client("ec2", region_name="us-east-1")
    ec2.create_volume(Size=500, AvailabilityZone="us-east-1a", VolumeType="gp2",
                      TagSpecifications=_tags("old-ml-dataset", 41, "volume"))
    ec2.allocate_address(Domain="vpc", TagSpecifications=_tags("test-ip-1", 60, "elastic-ip"))
    ec2.allocate_address(Domain="vpc", TagSpecifications=_tags("test-ip-2", 60, "elastic-ip"))
    rds = session.client("rds", region_name="us-east-1")
    rds.create_db_instance(DBInstanceIdentifier="fyp-database", DBInstanceClass="db.m5.large",
                           Engine="postgres", AllocatedStorage=100, MasterUsername="admin",
                           MasterUserPassword="demo-password-123", Tags=[_age(23)])
    _cpu(session.client("cloudwatch", region_name="us-east-1"), "AWS/RDS", "DatabaseConnections",
         "DBInstanceIdentifier", "fyp-database", 0)

    # us-west-2: the GPU someone spun up to "quickly test" an LLM
    _instance(session, "us-west-2", "g5.xlarge", "llm-finetune-test", 9, 0.4)

    # ap-south-1 (Mumbai): hackathon backend and a NAT gateway nobody remembers creating
    _instance(session, "ap-south-1", "t3.large", "hackathon-backend", 14, 0.8)
    ec2 = session.client("ec2", region_name="ap-south-1")
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnet = ec2.create_subnet(VpcId=vpc, CidrBlock="10.0.1.0/24")["Subnet"]["SubnetId"]
    alloc = ec2.allocate_address(Domain="vpc")["AllocationId"]
    ec2.create_nat_gateway(SubnetId=subnet, AllocationId=alloc,
                           TagSpecifications=_tags("fyp-vpc-nat", 30, "natgateway"))

    # eu-west-1: a scraper left running and a load balancer with nothing behind it
    _instance(session, "eu-west-1", "c5.xlarge", "web-scraper", 6, 1.1)
    ec2 = session.client("ec2", region_name="eu-west-1")
    vpc = ec2.create_vpc(CidrBlock="10.1.0.0/16")["Vpc"]["VpcId"]
    subnets = [ec2.create_subnet(VpcId=vpc, CidrBlock=f"10.1.{i}.0/24", AvailabilityZone=f"eu-west-1{z}")
               ["Subnet"]["SubnetId"] for i, z in ((1, "a"), (2, "b"))]
    session.client("elbv2", region_name="eu-west-1").create_load_balancer(Name="test-alb", Subnets=subnets)


def start():
    """Start (or restart) the simulated account and return a session for it."""
    global _mock
    if _mock is not None:
        _mock.stop()
    _mock = mock_aws()
    _mock.start()
    session = boto3.Session(aws_access_key_id="demo", aws_secret_access_key="demo", region_name="us-east-1")
    seed(session)
    return session


DEMO_REGIONS = ["us-east-1", "us-east-2", "us-west-1", "us-west-2", "ap-south-1", "ap-southeast-1",
                "ap-northeast-1", "eu-west-1", "eu-central-1", "eu-north-1", "me-south-1", "sa-east-1"]
