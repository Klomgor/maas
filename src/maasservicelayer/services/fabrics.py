# Copyright 2024-2025 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from typing import List

from maasservicelayer.builders.fabrics import FabricBuilder
from maasservicelayer.builders.vlans import VlanBuilder
from maasservicelayer.context import Context
from maasservicelayer.db.filters import QuerySpec
from maasservicelayer.db.repositories.fabrics import FabricsRepository
from maasservicelayer.db.repositories.subnets import SubnetClauseFactory
from maasservicelayer.db.repositories.vlans import VlansClauseFactory
from maasservicelayer.exceptions.catalog import (
    BadRequestException,
    BaseExceptionDetail,
)
from maasservicelayer.exceptions.constants import (
    CANNOT_DELETE_DEFAULT_FABRIC_VIOLATION_TYPE,
    CANNOT_DELETE_FABRIC_WITH_CONNECTED_INTERFACE_VIOLATION_TYPE,
    CANNOT_DELETE_FABRIC_WITH_SUBNETS_VIOLATION_TYPE,
)
from maasservicelayer.models.fabrics import Fabric
from maasservicelayer.services.base import BaseService
from maasservicelayer.services.interfaces import InterfacesService
from maasservicelayer.services.subnets import SubnetsService
from maasservicelayer.services.vlans import (
    DEFAULT_MTU,
    DEFAULT_VID,
    VlansService,
)


class FabricsService(BaseService[Fabric, FabricsRepository, FabricBuilder]):
    def __init__(
        self,
        context: Context,
        vlans_service: VlansService,
        subnets_service: SubnetsService,
        interfaces_service: InterfacesService,
        fabrics_repository: FabricsRepository,
    ):
        super().__init__(context, fabrics_repository)
        self.vlans_service = vlans_service
        self.subnets_service = subnets_service
        self.interfaces_service = interfaces_service

    async def post_create_hook(self, resource: Fabric) -> None:
        # Create default VLAN for new Fabric
        await self.vlans_service.create(
            builder=VlanBuilder(
                vid=DEFAULT_VID,
                name="Default VLAN",
                description="",
                fabric_id=resource.id,
                mtu=DEFAULT_MTU,
                dhcp_on=False,
            )
        )

    async def pre_delete_hook(self, resource_to_be_deleted: Fabric) -> None:
        if resource_to_be_deleted.id == 0:
            raise BadRequestException(
                details=[
                    BaseExceptionDetail(
                        type=CANNOT_DELETE_DEFAULT_FABRIC_VIOLATION_TYPE,
                        message="The default Fabric (id=0) cannot be deleted.",
                    )
                ]
            )

        # Can't delete the Fabric if it still has any Subnets
        subnets_in_fabric = await self.subnets_service.exists(
            query=QuerySpec(
                where=SubnetClauseFactory.with_fabric_id(
                    resource_to_be_deleted.id
                )
            )
        )
        if subnets_in_fabric:
            raise BadRequestException(
                details=[
                    BaseExceptionDetail(
                        type=CANNOT_DELETE_FABRIC_WITH_SUBNETS_VIOLATION_TYPE,
                        message=f"The Fabric {resource_to_be_deleted.id} cannot be deleted as it still has subnets. Please "
                        f"delete the subnets first.",
                    )
                ]
            )

        # Can't delete the Fabric if any interfaces are still connected
        attached_interfaces = (
            await self.interfaces_service.get_interfaces_in_fabric(
                fabric_id=resource_to_be_deleted.id
            )
        )

        if len(attached_interfaces) > 0:
            interface_names = ", ".join(
                [interface.name for interface in attached_interfaces]
            )
            raise BadRequestException(
                details=[
                    BaseExceptionDetail(
                        type=CANNOT_DELETE_FABRIC_WITH_CONNECTED_INTERFACE_VIOLATION_TYPE,
                        message=f"The Fabric {resource_to_be_deleted.id} still has connected interfaces: {interface_names}.",
                    )
                ]
            )

    async def post_delete_hook(self, resource: Fabric) -> None:
        # TODO: Replace this with VlansService.delete_many once
        #       VlansService.post_delete_many_hook is implemented.
        vlans_to_delete = await self.vlans_service.get_many(
            query=QuerySpec(
                where=VlansClauseFactory.with_fabric_id(resource.id)
            )
        )
        for vlan in vlans_to_delete:
            await self.vlans_service.delete_by_id(
                id=vlan.id,
                etag_if_match=vlan.etag(),
                force=True,
            )

    async def post_delete_many_hook(self, resources: List[Fabric]) -> None:
        raise NotImplementedError("Not implemented yet.")
