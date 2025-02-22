#  Copyright 2024-2025 Canonical Ltd.  This software is licensed under the
#  GNU Affero General Public License version 3 (see the file LICENSE).

from datetime import datetime

from netaddr import IPNetwork
from pydantic import IPvAnyAddress

from maascommon.enums.ipaddress import (
    IpAddressFamily,
    IpAddressType,
    LeaseAction,
)
from maasservicelayer.builders.staticipaddress import StaticIPAddressBuilder
from maasservicelayer.context import Context
from maasservicelayer.models.interfaces import Interface
from maasservicelayer.models.leases import Lease
from maasservicelayer.models.staticipaddress import StaticIPAddress
from maasservicelayer.models.subnets import Subnet
from maasservicelayer.services.base import Service
from maasservicelayer.services.dnsresources import DNSResourcesService
from maasservicelayer.services.interfaces import InterfacesService
from maasservicelayer.services.ipranges import IPRangesService
from maasservicelayer.services.nodes import NodesService
from maasservicelayer.services.staticipaddress import StaticIPAddressService
from maasservicelayer.services.subnets import SubnetsService


class LeaseUpdateError(Exception):
    pass


def _is_valid_hostname(hostname):
    return (
        hostname is not None
        and len(hostname) > 0
        and not hostname.isspace()
        and hostname != "(none)"
    )


class LeasesService(Service):
    def __init__(
        self,
        context: Context,
        dnsresource_service: DNSResourcesService,
        node_service: NodesService,
        staticipaddress_service: StaticIPAddressService,
        subnet_service: SubnetsService,
        interface_service: InterfacesService,
        iprange_service: IPRangesService,
    ):
        super().__init__(context)
        self.dnsresource_service = dnsresource_service
        self.node_service = node_service
        self.staticipaddress_service = staticipaddress_service
        self.subnet_service = subnet_service
        self.interface_service = interface_service
        self.iprange_service = iprange_service

    async def store_lease_info(self, lease: Lease) -> None:
        subnet = await self.subnet_service.find_best_subnet_for_ip(lease.ip)

        if subnet is None:
            raise LeaseUpdateError(f"No subnet exists for: {lease.ip}")

        subnet_network = IPNetwork(str(subnet.cidr))
        if lease.ip_family != subnet_network.version:
            raise LeaseUpdateError(
                f"Family for the subnet does not match. Expected: {lease.ip_family}"
            )

        created = datetime.fromtimestamp(lease.timestamp_epoch)

        # We will receive actions on all addresses in the subnet. We only want
        # to update the addresses in the dynamic range.
        dynamic_range = await self.iprange_service.get_dynamic_range_for_ip(
            subnet, lease.ip
        )
        if dynamic_range is None:
            return

        interfaces = await self.interface_service.get_interfaces_for_mac(
            lease.mac
        )
        if len(interfaces) == 0:
            if lease.action != LeaseAction.COMMIT:
                return

        sip = None
        old_family_addresses = await self.staticipaddress_service.get_discovered_ips_in_family_for_interfaces(
            interfaces,
            family=(
                IpAddressFamily.IPV4
                if subnet_network.version == IpAddressFamily.IPV4.value
                else IpAddressFamily.IPV6
            ),
        )
        for address in old_family_addresses:
            if address.ip != lease.ip:
                if address.ip is not None:
                    await self.dnsresource_service.release_dynamic_hostname(
                        address
                    )
                    await self.staticipaddress_service.delete_by_id(address.id)
                else:
                    sip = address

        match lease.action:
            case LeaseAction.COMMIT.value:
                await self._commit_lease_info(
                    lease.hostname,
                    subnet,
                    lease.ip,
                    lease.lease_time_seconds,
                    created,
                    interfaces,
                )
            case LeaseAction.EXPIRY.value:
                await self._release_lease_info(sip, interfaces, subnet)
            case LeaseAction.RELEASE.value:
                await self._release_lease_info(sip, interfaces, subnet)

    async def _commit_lease_info(
        self,
        hostname: str,
        subnet: Subnet,
        ip: IPvAnyAddress,
        lease_time: int,
        created: datetime,
        interfaces: list[Interface],
    ) -> None:
        sip_hostname = None
        if _is_valid_hostname(hostname):
            sip_hostname = hostname

        sip = await self.staticipaddress_service.create_or_update(
            StaticIPAddressBuilder(
                ip=ip,
                lease_time=lease_time,
                alloc_type=IpAddressType.DISCOVERED,
                subnet_id=subnet.id,
            )
        )

        for interface in interfaces:
            await self.interface_service.add_ip(interface, sip)
        if sip_hostname is not None:
            node_with_hostname_exists = (
                await self.node_service.hostname_exists(sip_hostname)
            )
            if node_with_hostname_exists:
                await self.dnsresource_service.release_dynamic_hostname(sip)
            else:
                await self.dnsresource_service.update_dynamic_hostname(
                    sip, sip_hostname
                )

    async def _release_lease_info(
        self, sip: StaticIPAddress, interfaces: list[Interface], subnet: Subnet
    ) -> None:
        if sip is None:
            sip = await self.staticipaddress_service.get_for_interfaces(
                interfaces,
                subnet=subnet,
                ip=None,
                alloc_type=IpAddressType.DISCOVERED.value,
            )

            if sip is None:
                sip = await self.staticipaddress_service.create(
                    StaticIPAddressBuilder(
                        ip=None,
                        lease_time=0,
                        alloc_type=IpAddressType.DISCOVERED,
                        subnet_id=subnet.id,
                    )
                )
        else:
            await self.staticipaddress_service.update_by_id(
                sip.id,
                StaticIPAddressBuilder(
                    ip=sip.ip,
                    lease_time=sip.lease_time,
                    alloc_type=sip.alloc_type,
                    subnet_id=sip.subnet_id,
                    created=sip.created,
                ),
            )

        await self.interface_service.bulk_link_ip(sip, interfaces)
