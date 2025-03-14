#  Copyright 2024-2025 Canonical Ltd.  This software is licensed under the
#  GNU Affero General Public License version 3 (see the file LICENSE).

from typing import List

from pydantic import IPvAnyAddress

from maascommon.workflows.dhcp import (
    CONFIGURE_DHCP_WORKFLOW_NAME,
    ConfigureDHCPParam,
    merge_configure_dhcp_param,
)
from maasservicelayer.builders.ipranges import IPRangeBuilder
from maasservicelayer.context import Context
from maasservicelayer.db.filters import QuerySpec
from maasservicelayer.db.repositories.dhcpsnippets import (
    DhcpSnippetsClauseFactory,
)
from maasservicelayer.db.repositories.ipranges import (
    IPRangeClauseFactory,
    IPRangesRepository,
)
from maasservicelayer.exceptions.catalog import (
    AlreadyExistsException,
    BaseExceptionDetail,
)
from maasservicelayer.exceptions.constants import (
    UNIQUE_CONSTRAINT_VIOLATION_TYPE,
)
from maasservicelayer.models.ipranges import IPRange
from maasservicelayer.services.base import BaseService
from maasservicelayer.services.dhcpsnippets import DhcpSnippetsService
from maasservicelayer.services.temporal import TemporalService


class IPRangesService(
    BaseService[IPRange, IPRangesRepository, IPRangeBuilder]
):
    def __init__(
        self,
        context: Context,
        temporal_service: TemporalService,
        dhcpsnippets_service: DhcpSnippetsService,
        ipranges_repository: IPRangesRepository,
    ):
        super().__init__(context, ipranges_repository)
        self.temporal_service = temporal_service
        self.dhcpsnippets_service = dhcpsnippets_service
        self.ipranges_repository = ipranges_repository

    async def get_dynamic_range_for_ip(
        self, subnet_id: int, ip: IPvAnyAddress
    ) -> IPRange | None:
        return await self.repository.get_dynamic_range_for_ip(subnet_id, ip)

    async def pre_create_hook(self, builder: IPRangeBuilder) -> None:
        iprange = await self.exists(
            query=QuerySpec(
                where=IPRangeClauseFactory.and_clauses(
                    [
                        # TODO: remove type ignore after implementing safe get for builders
                        IPRangeClauseFactory.with_type(builder.type),  # type: ignore
                        IPRangeClauseFactory.with_start_ip(builder.start_ip),  # type: ignore
                        IPRangeClauseFactory.with_start_ip(builder.end_ip),  # type: ignore
                        IPRangeClauseFactory.with_subnet_id(builder.subnet_id),  # type: ignore
                    ]
                )
            )
        )
        if iprange:
            raise AlreadyExistsException(
                details=[
                    BaseExceptionDetail(
                        type=UNIQUE_CONSTRAINT_VIOLATION_TYPE,
                        message="An IP range with such identifiers already exist.",
                    )
                ]
            )

    async def post_create_hook(self, resource: IPRange) -> None:
        self.temporal_service.register_or_update_workflow_call(
            CONFIGURE_DHCP_WORKFLOW_NAME,
            ConfigureDHCPParam(ip_range_ids=[resource.id]),
            parameter_merge_func=merge_configure_dhcp_param,
            wait=False,
        )
        return

    async def post_update_hook(
        self, old_resource: IPRange, updated_resource: IPRange
    ) -> None:
        self.temporal_service.register_or_update_workflow_call(
            CONFIGURE_DHCP_WORKFLOW_NAME,
            ConfigureDHCPParam(ip_range_ids=[updated_resource.id]),
            parameter_merge_func=merge_configure_dhcp_param,
            wait=False,
        )
        return

    async def post_update_many_hook(self, resources: List[IPRange]) -> None:
        raise NotImplementedError("Not implemented yet.")

    async def post_delete_hook(self, resource: IPRange) -> None:
        await self.dhcpsnippets_service.delete_many(
            query=QuerySpec(
                where=DhcpSnippetsClauseFactory.with_iprange_id(resource.id)
            )
        )
        self.temporal_service.register_or_update_workflow_call(
            CONFIGURE_DHCP_WORKFLOW_NAME,
            ConfigureDHCPParam(subnet_ids=[resource.subnet_id]),
            parameter_merge_func=merge_configure_dhcp_param,
            wait=False,
        )

    async def post_delete_many_hook(self, resources: List[IPRange]) -> None:
        raise NotImplementedError("Not implemented yet.")

    async def update_many(
        self, query: QuerySpec, builder: IPRangeBuilder
    ) -> List[IPRange]:
        updated_resources = await self.repository.update_many(
            query=query, builder=builder
        )

        if builder.must_trigger_workflow():
            await self.post_update_many_hook(updated_resources)
        return updated_resources
