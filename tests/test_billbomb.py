import boto3
import pytest
from moto import mock_aws

from billbomb import actions, demo, pricing, scanner

REGIONS = ["us-east-1", "us-west-2", "ap-south-1", "eu-west-1"]


@pytest.fixture(scope="module")
def account():
    with mock_aws():
        session = boto3.Session(aws_access_key_id="t", aws_secret_access_key="t", region_name="us-east-1")
        demo.seed(session)
        yield session


def by_name(findings):
    return {f["name"]: f for f in findings}


def test_finds_zombies_across_regions(account):
    found = by_name(scanner.scan(account, REGIONS, demo=True)["findings"])
    gpu = found["llm-finetune-test"]
    assert gpu["zombie"] and gpu["region"] == "us-west-2"
    assert any("GPU" in r for r in gpu["reasons"])
    assert found["fyp-database"]["zombie"]
    assert found["old-ml-dataset"]["kind"] == "EBS volume"
    assert found["fyp-vpc-nat"]["kind"] == "NAT gateway"


def test_server_in_use_is_left_alone(account):
    site = by_name(scanner.scan(account, REGIONS, demo=True)["findings"])["my-portfolio-site"]
    assert not site["zombie"]
    assert site["reasons"] == []


def test_zombies_sorted_first_by_cost(account):
    findings = scanner.scan(account, REGIONS, demo=True)["findings"]
    zombies = [f for f in findings if f["zombie"]]
    assert findings[: len(zombies)] == zombies
    assert zombies[0]["name"] == "llm-finetune-test"


def test_summary_maths():
    findings = [
        {"monthly_cost": 100.0, "zombie": True, "region": "us-east-1"},
        {"monthly_cost": 50.0, "zombie": False, "region": "eu-west-1"},
    ]
    s = scanner.summarize(findings, budget_usd=10, pkr_rate=280)
    assert s["monthly_usd"] == 150 and s["wasted_usd"] == 100
    assert s["monthly_pkr"] == 42000
    assert s["days_to_budget"] == 2.0
    assert s["zombies"] == 1


def test_pricing_estimates():
    assert pricing.ec2_monthly("g5.xlarge") == pytest.approx(734.38, abs=0.5)
    assert pricing.is_gpu("p4d.24xlarge") and not pricing.is_gpu("t3.micro")
    assert pricing.ec2_monthly("x9.mystery-xlarge") > 0


def test_defuse_removes_cost(account):
    findings = scanner.scan(account, ["us-east-1"], demo=True)["findings"]
    volume = by_name(findings)["old-ml-dataset"]
    ok, message = actions.defuse(account, volume)
    assert ok, message
    after = by_name(scanner.scan(account, ["us-east-1"], demo=True)["findings"])
    assert "old-ml-dataset" not in after
