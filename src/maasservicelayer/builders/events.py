# Copyright 2025 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from datetime import datetime
from typing import Union

from pydantic import Field
from pydantic.networks import IPvAnyAddress

from maasservicelayer.models.base import ResourceBuilder, UNSET, Unset
from maasservicelayer.models.events import (
    EndpointChoicesEnum,
    EventType,
    LoggingLevelEnum,
)


class EventBuilder(ResourceBuilder):
    """Autogenerated from utilities/generate_builders.py.

    You can still add your custom methods here, they won't be overwritten by
    the generated code.
    """

    action: Union[str, Unset] = Field(default=UNSET, required=False)
    created: Union[datetime, Unset] = Field(default=UNSET, required=False)
    description: Union[str, Unset] = Field(default=UNSET, required=False)
    endpoint: Union[EndpointChoicesEnum, Unset] = Field(
        default=UNSET, required=False
    )
    id: Union[int, Unset] = Field(default=UNSET, required=False)
    ip_address: Union[IPvAnyAddress, None, Unset] = Field(
        default=UNSET, required=False
    )
    node_hostname: Union[str, Unset] = Field(default=UNSET, required=False)
    node_id: Union[int, None, Unset] = Field(default=UNSET, required=False)
    node_system_id: Union[str, None, Unset] = Field(
        default=UNSET, required=False
    )
    owner: Union[str, Unset] = Field(default=UNSET, required=False)
    type: Union[EventType, Unset] = Field(default=UNSET, required=False)
    updated: Union[datetime, Unset] = Field(default=UNSET, required=False)
    user_agent: Union[str, Unset] = Field(default=UNSET, required=False)
    user_id: Union[int, None, Unset] = Field(default=UNSET, required=False)


class EventTypeBuilder(ResourceBuilder):
    """Autogenerated from utilities/generate_builders.py.

    You can still add your custom methods here, they won't be overwritten by
    the generated code.
    """

    created: Union[datetime, Unset] = Field(default=UNSET, required=False)
    description: Union[str, Unset] = Field(default=UNSET, required=False)
    id: Union[int, Unset] = Field(default=UNSET, required=False)
    level: Union[LoggingLevelEnum, Unset] = Field(
        default=UNSET, required=False
    )
    name: Union[str, Unset] = Field(default=UNSET, required=False)
    updated: Union[datetime, Unset] = Field(default=UNSET, required=False)
