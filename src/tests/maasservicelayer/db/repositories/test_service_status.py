# Copyright 2024-2025 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from maascommon.enums.service import ServiceName, ServiceStatusEnum
from maasservicelayer.builders.service_status import ServiceStatusBuilder
from maasservicelayer.context import Context
from maasservicelayer.db.repositories.service_status import (
    ServiceStatusClauseFactory,
    ServiceStatusRepository,
)
from maasservicelayer.models.service_status import ServiceStatus
from tests.fixtures.factories.node import create_test_region_controller_entry
from tests.fixtures.factories.service_status import (
    create_test_service_status_entry,
)
from tests.maasapiserver.fixtures.db import Fixture
from tests.maasservicelayer.db.repositories.base import RepositoryCommonTests


class TestServiceStatusClauseFactory:
    def test_builder(self) -> None:
        clause = ServiceStatusClauseFactory.with_node_id(1)
        assert str(
            clause.condition.compile(compile_kwargs={"literal_binds": True})
        ) == ("maasserver_service.node_id = 1")

        clause = ServiceStatusClauseFactory.with_name(ServiceName.HTTP)
        assert str(
            clause.condition.compile(compile_kwargs={"literal_binds": True})
        ) == ("maasserver_service.name = 'http'")


class TestServiceStatusRepository(RepositoryCommonTests[ServiceStatus]):
    @pytest.fixture
    def repository_instance(
        self, db_connection: AsyncConnection
    ) -> ServiceStatusRepository:
        return ServiceStatusRepository(Context(connection=db_connection))

    @pytest.fixture
    async def _setup_test_list(
        self, fixture: Fixture, num_objects: int
    ) -> list[ServiceStatus]:
        node = await create_test_region_controller_entry(fixture)
        all_names = list(ServiceName)
        size_names = len(all_names)
        created_service_status = [
            await create_test_service_status_entry(
                fixture,
                name=all_names[i % size_names],
                description=str(i),
                node_id=node["id"],
            )
            for i in range(num_objects)
        ]
        return created_service_status

    @pytest.fixture
    async def created_instance(self, fixture: Fixture) -> ServiceStatus:
        return await create_test_service_status_entry(fixture)

    @pytest.fixture
    async def instance_builder_model(self) -> type[ServiceStatusBuilder]:
        return ServiceStatusBuilder

    @pytest.fixture
    async def instance_builder(self, fixture: Fixture) -> ServiceStatusBuilder:
        node = await create_test_region_controller_entry(fixture)
        return ServiceStatusBuilder(
            name=ServiceName.HTTP,
            status=ServiceStatusEnum.RUNNING,
            node_id=node["id"],
            status_info="",
        )

    @pytest.mark.skip(reason="The only unique key is the ID.")
    async def test_create_duplicated(
        self, repository_instance, instance_builder
    ):
        pass
