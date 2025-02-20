#  Copyright 2024-2025 Canonical Ltd.  This software is licensed under the
#  GNU Affero General Public License version 3 (see the file LICENSE).

from typing import List

from maascommon.enums.ipaddress import IpAddressType
from maascommon.workflows.dhcp import (
    CONFIGURE_DHCP_WORKFLOW_NAME,
    ConfigureDHCPParam,
    merge_configure_dhcp_param,
)
from maasservicelayer.context import Context
from maasservicelayer.db.repositories.interfaces import InterfaceRepository
from maasservicelayer.models.base import ListResult
from maasservicelayer.models.interfaces import Interface
from maasservicelayer.models.staticipaddress import StaticIPAddress
from maasservicelayer.services.base import Service
from maasservicelayer.services.temporal import TemporalService


class InterfacesService(Service):
    def __init__(
        self,
        context: Context,
        temporal_service: TemporalService,
        interface_repository: InterfaceRepository,
    ):
        super().__init__(context)
        self.temporal_service = temporal_service
        self.interface_repository = interface_repository

    async def list(
        self, node_id: int, page: int, size: int
    ) -> ListResult[Interface]:
        return await self.interface_repository.list(
            node_id=node_id, page=page, size=size
        )

    async def get_interfaces_for_mac(self, mac: str) -> List[Interface]:
        return await self.interface_repository.get_interfaces_for_mac(mac)

    async def get_interfaces_in_fabric(
        self, fabric_id: int
    ) -> List[Interface]:
        return await self.interface_repository.get_interfaces_in_fabric(
            fabric_id=fabric_id
        )

    async def bulk_link_ip(
        self, sip: StaticIPAddress, interfaces: List[Interface]
    ) -> None:
        for interface in interfaces:
            await self.interface_repository.add_ip(interface, sip)

    async def add_ip(self, interface: Interface, sip: StaticIPAddress) -> None:
        await self.interface_repository.add_ip(interface, sip)
        if sip.alloc_type in (
            IpAddressType.AUTO,
            IpAddressType.STICKY,
            IpAddressType.USER_RESERVED,
        ):
            self.temporal_service.register_or_update_workflow_call(
                CONFIGURE_DHCP_WORKFLOW_NAME,
                ConfigureDHCPParam(static_ip_addr_ids=[sip.id]),
                parameter_merge_func=merge_configure_dhcp_param,
                wait=False,
            )
