#  Copyright 2024-2025 Canonical Ltd.  This software is licensed under the
#  GNU Affero General Public License version 3 (see the file LICENSE).

from ipaddress import IPv4Address, IPv4Network, IPv6Address

from pydantic import IPvAnyAddress, ValidationError
import pytest

from maasapiserver.v3.api.public.models.requests.subnets import SubnetRequest
from maascommon.bootmethods import BOOT_METHODS_METADATA
from maascommon.enums.subnet import RdnsMode
from maasservicelayer.exceptions.catalog import ValidationException
from maasservicelayer.models.fields import IPv4v6Network


class TestSubnetRequest:
    def test_mandatory_params(self) -> None:
        with pytest.raises(ValidationError) as e:
            SubnetRequest()

        assert len(e.value.errors()) == 1
        assert e.value.errors()[0]["loc"][0] == "cidr"

    @pytest.mark.parametrize(
        "cidr, is_valid,",
        [
            ("10.0.0.1", True),
            ("10.0.0.1/32", True),
            ("10.0.0.1/0", False),
            ("10.0.0.1/33", False),
            ("10.0.0.256", False),
        ],
    )
    def test_cidr_validation(
        self, cidr: IPv4v6Network, is_valid: bool
    ) -> None:
        if is_valid:
            SubnetRequest(cidr=cidr)
        else:
            with pytest.raises(ValueError):
                SubnetRequest(cidr=cidr)

    @pytest.mark.parametrize(
        "gateway_ip, cidr, is_valid,",
        [
            (IPv4Address("10.0.0.1"), "10.0.0.1/24", True),
            (IPv4Address("10.0.0.2"), "10.0.0.1/32", False),
            (IPv4Address("10.0.0.0"), "192.168.1.0/24", False),
            (IPv6Address("fe80::3"), "2001:db00::0/24", True),
            (IPv6Address("fe80::3"), "10.0.0.1/24", False),
        ],
    )
    def test_gateway_ip_validation(
        self, gateway_ip: IPvAnyAddress, cidr: IPv4v6Network, is_valid: bool
    ) -> None:
        if is_valid:
            SubnetRequest(cidr=cidr, gateway_ip=gateway_ip)
        else:
            with pytest.raises(ValueError):
                SubnetRequest(cidr=cidr, gateway_ip=gateway_ip)

    def test_defaults(self):
        request = SubnetRequest(cidr=IPv4Network("10.0.0.1"))
        assert request.name is None
        assert request.description == ""
        assert request.rdns_mode == RdnsMode.DEFAULT
        assert request.gateway_ip is None
        assert request.dns_servers == []
        assert request.allow_dns is True
        assert request.allow_proxy is True
        assert request.active_discovery is True
        assert request.managed is True
        assert request.disabled_boot_architectures == []

    def test_to_builder(self) -> None:
        builder = SubnetRequest(cidr=IPv4Network("10.0.0.1")).to_builder(0)
        assert builder.name == "10.0.0.1/32"
        assert builder.description == ""
        assert builder.cidr == IPv4Network("10.0.0.1")

    @pytest.mark.parametrize(
        "arch, is_valid,",
        [
            *[
                (method.arch_octet, True)
                for method in BOOT_METHODS_METADATA
                if method.arch_octet is not None
            ],
            *[
                (method.name, True)
                for method in BOOT_METHODS_METADATA
                if method.name not in [None, "windows"]
            ],
            ("test", False),
        ],
    )
    def test_disabled_boot_architectures(
        self, arch: str, is_valid: bool
    ) -> None:
        if is_valid:
            SubnetRequest(
                cidr=IPv4Network("10.0.0.1"),
                disabled_boot_architectures=[arch],
            ).to_builder(0)
        else:
            with pytest.raises(ValidationException):
                SubnetRequest(
                    cidr=IPv4Network("10.0.0.1"),
                    disabled_boot_architectures=[arch],
                ).to_builder(0)
