#  Copyright 2024 Canonical Ltd.  This software is licensed under the
#  GNU Affero General Public License version 3 (see the file LICENSE).

from sqlalchemy import select
from sqlalchemy.sql.operators import eq

from maasservicelayer.db.repositories.base import Repository
from maasservicelayer.db.tables import ConfigTable
from maasservicelayer.models.configurations import Configuration


class ConfigurationsRepository(Repository):
    async def get(self, name: str) -> Configuration | None:
        stmt = (
            select(
                "*",
            )
            .select_from(ConfigTable)
            .where(eq(ConfigTable.c.name, name))
        )
        result = (await self.execute_stmt(stmt)).one_or_none()
        return Configuration(**result._asdict()) if result else None
