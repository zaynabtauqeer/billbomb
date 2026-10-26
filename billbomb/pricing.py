"""Approximate on-demand AWS prices (USD, us-east-1, Linux), good enough to spot a bill bomb.

Prices vary by region and change over time; BillBomb labels every figure as an
estimate. Update this table or add rows for anything missing.
"""

HOURS_PER_MONTH = 730

EC2_HOURLY = {
    "t2.micro": 0.0116, "t2.small": 0.023, "t2.medium": 0.0464, "t2.large": 0.0928,
    "t3.nano": 0.0052, "t3.micro": 0.0104, "t3.small": 0.0208, "t3.medium": 0.0416,
    "t3.large": 0.0832, "t3.xlarge": 0.1664, "t3.2xlarge": 0.3328,
    "t4g.micro": 0.0084, "t4g.small": 0.0168, "t4g.medium": 0.0336,
    "m5.large": 0.096, "m5.xlarge": 0.192, "m5.2xlarge": 0.384, "m6i.large": 0.096,
    "c5.large": 0.085, "c5.xlarge": 0.17, "c5.2xlarge": 0.34,
    "r5.large": 0.126, "r5.xlarge": 0.252,
    "g4dn.xlarge": 0.526, "g4dn.2xlarge": 0.752, "g5.xlarge": 1.006, "g5.2xlarge": 1.212,
    "g5.12xlarge": 5.672, "p3.2xlarge": 3.06, "p4d.24xlarge": 32.77, "p5.48xlarge": 98.32,
}
GPU_FAMILIES = ("g4", "g5", "g6", "p3", "p4", "p5", "inf", "trn")

RDS_HOURLY = {
    "db.t3.micro": 0.017, "db.t3.small": 0.034, "db.t3.medium": 0.068, "db.t3.large": 0.136,
    "db.t4g.micro": 0.016, "db.m5.large": 0.171, "db.m5.xlarge": 0.342, "db.r5.large": 0.24,
}

EBS_GB_MONTH = {"gp2": 0.10, "gp3": 0.08, "io1": 0.125, "io2": 0.125, "st1": 0.045, "sc1": 0.015, "standard": 0.05}
SNAPSHOT_GB_MONTH = 0.05
PUBLIC_IPV4_HOURLY = 0.005          # every public IPv4 address, since Feb 2024
NAT_GATEWAY_HOURLY = 0.045
LOAD_BALANCER_HOURLY = 0.0225


def monthly(hourly):
    return round(hourly * HOURS_PER_MONTH, 2)


def ec2_monthly(instance_type):
    """Unknown types get a conservative estimate from their size suffix."""
    if instance_type in EC2_HOURLY:
        return monthly(EC2_HOURLY[instance_type])
    size = instance_type.split(".")[-1]
    guess = {"nano": 0.005, "micro": 0.01, "small": 0.02, "medium": 0.04, "large": 0.09,
             "xlarge": 0.18, "2xlarge": 0.36, "4xlarge": 0.72}.get(size, 0.5)
    if is_gpu(instance_type):
        guess = max(guess, 1.0)
    return monthly(guess)


def rds_monthly(db_class):
    return monthly(RDS_HOURLY.get(db_class, 0.2))


def is_gpu(instance_type):
    return instance_type.startswith(GPU_FAMILIES)
