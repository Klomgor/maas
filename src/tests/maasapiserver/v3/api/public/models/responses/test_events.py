#  Copyright 2024 Canonical Ltd.  This software is licensed under the
#  GNU Affero General Public License version 3 (see the file LICENSE).

from ipaddress import IPv4Address

from maasapiserver.v3.api.public.models.responses.events import (
    EventResponse,
    EventTypeLevelEnum,
)
from maasapiserver.v3.constants import V3_API_PREFIX
from maasservicelayer.models.events import (
    EndpointChoicesEnum,
    Event,
    EventType,
    LoggingLevelEnum,
)
from maasservicelayer.utils.date import utcnow


class TestEventResponse:
    def test_from_model(self) -> None:
        now = utcnow()
        event = Event(
            id=1,
            created=now,
            updated=now,
            type=EventType(
                id=1,
                created=now,
                updated=now,
                name="type test",
                description="type description",
                level=LoggingLevelEnum.AUDIT,
            ),
            node_system_id="test",
            node_hostname="hostname",
            user_id=1,
            owner="test",
            ip_address=IPv4Address("127.0.0.1"),
            endpoint=EndpointChoicesEnum.API,
            user_agent="agent",
            description="descr",
            action="deploy",
        )
        response = EventResponse.from_model(
            event=event, self_base_hyperlink=f"{V3_API_PREFIX}/events"
        )
        assert event.id == response.id
        assert event.created == response.created
        assert event.updated == response.updated
        assert event.node_system_id == response.node_system_id
        assert event.node_hostname == response.node_hostname
        assert event.user_id == response.user_id
        assert event.owner == response.owner
        assert event.ip_address == response.ip_address
        assert event.user_agent == response.user_agent
        assert event.description == response.description
        assert event.action == response.action
        assert event.type.name == response.type.name
        assert event.type.description == response.type.description
        assert response.type.level == EventTypeLevelEnum.AUDIT
        assert (
            response.hal_links.self.href
            == f"{V3_API_PREFIX}/events/{event.id}"
        )
