# Copyright 2024 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from typing import Self, Type

from pydantic import IPvAnyAddress
from sqlalchemy import and_, cast, join, select, Table
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.sql.expression import exists
from sqlalchemy.sql.operators import eq

from maasservicelayer.db.filters import Clause, ClauseFactory
from maasservicelayer.db.repositories.base import (
    BaseRepository,
    ResourceBuilder,
)
from maasservicelayer.db.tables import ReservedIPTable, SubnetTable, VlanTable
from maasservicelayer.models.fields import MacAddress
from maasservicelayer.models.reservedips import ReservedIP


class ReservedIPsClauseFactory(ClauseFactory):
    @classmethod
    def with_id(cls, id: int) -> Clause:
        return Clause(condition=eq(ReservedIPTable.c.id, id))

    @classmethod
    def with_subnet_id(cls, subnet_id: int) -> Clause:
        return Clause(condition=eq(ReservedIPTable.c.subnet_id, subnet_id))

    @classmethod
    def with_vlan_id(cls, vlan_id: int) -> Clause:
        return Clause(
            condition=eq(SubnetTable.c.vlan_id, vlan_id),
            joins=[
                join(
                    ReservedIPTable,
                    SubnetTable,
                    eq(ReservedIPTable.c.subnet_id, SubnetTable.c.id),
                )
            ],
        )

    @classmethod
    def with_fabric_id(cls, fabric_id: int) -> Clause:
        return Clause(
            condition=eq(VlanTable.c.fabric_id, fabric_id),
            joins=[
                join(
                    ReservedIPTable,
                    SubnetTable,
                    eq(ReservedIPTable.c.subnet_id, SubnetTable.c.id),
                ),
                join(
                    VlanTable,
                    SubnetTable,
                    eq(VlanTable.c.id, SubnetTable.c.vlan_id),
                ),
            ],
        )


class ReservedIPsResourceBuilder(ResourceBuilder):
    def with_ip(self, ip: IPvAnyAddress) -> Self:
        self._request.set_value(ReservedIPTable.c.ip.name, ip)
        return self

    def with_mac_address(self, mac_address: MacAddress) -> Self:
        self._request.set_value(
            ReservedIPTable.c.mac_address.name, mac_address
        )
        return self

    def with_comment(self, comment: str | None) -> Self:
        self._request.set_value(ReservedIPTable.c.comment.name, comment)
        return self

    def with_subnet_id(self, subnet_id: int) -> Self:
        self._request.set_value(ReservedIPTable.c.subnet_id.name, subnet_id)
        return self


class ReservedIPsRepository(BaseRepository[ReservedIP]):
    def get_repository_table(self) -> Table:
        return ReservedIPTable

    def get_model_factory(self) -> Type[ReservedIP]:
        return ReservedIP

    async def exists_within_subnet_ip_range(
        self, subnet_id: int, start_ip: IPvAnyAddress, end_ip: IPvAnyAddress
    ) -> bool:
        stmt = select(1).where(
            and_(
                eq(ReservedIPTable.c.subnet_id, subnet_id),
                cast(ReservedIPTable.c.ip, INET).between(start_ip, end_ip),
            )
        )
        stmt = exists(stmt).select()
        return (await self.connection.execute(stmt)).scalar()