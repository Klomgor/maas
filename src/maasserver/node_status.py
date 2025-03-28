# Copyright 2014-2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Node status utilities."""

__all__ = [
    "NODE_TRANSITIONS",
    "get_failed_status",
    "get_node_timeout",
    "is_failed_status",
]


from maascommon.node import NODE_FAILURE_STATUS_TRANSITION_MAP
from maasserver.enum import NODE_STATUS
from maasserver.models.config import Config
from provisioningserver.utils.enum import map_enum

# State transitions for where running testing will automatically reset the node
# to a READY state on passing. Upon failure the node is set to FAILED_TESTING.
# This is a subset of the statuses NODE_STATUS.TESTING can transition to. This
# allows testing to be aborted.
NODE_TESTING_RESET_READY_TRANSITIONS = {
    NODE_STATUS.NEW,
    NODE_STATUS.COMMISSIONING,
    NODE_STATUS.FAILED_DEPLOYMENT,
    NODE_STATUS.MISSING,
    NODE_STATUS.RETIRED,
    NODE_STATUS.BROKEN,
    NODE_STATUS.FAILED_RELEASING,
    NODE_STATUS.FAILED_DISK_ERASING,
    NODE_STATUS.FAILED_ENTERING_RESCUE_MODE,
    NODE_STATUS.FAILED_EXITING_RESCUE_MODE,
    NODE_STATUS.FAILED_TESTING,
}


# Define valid node status transitions. This is enforced in the code, so
# get it right.
#
# The format is:
# {
#  old_status1: [
#      new_status11,
#      new_status12,
#      new_status13,
#      ],
# ...
# }
#
NODE_TRANSITIONS = {
    None: [NODE_STATUS.NEW, NODE_STATUS.MISSING, NODE_STATUS.RETIRED],
    NODE_STATUS.NEW: [
        NODE_STATUS.COMMISSIONING,
        NODE_STATUS.TESTING,
        NODE_STATUS.MISSING,
        NODE_STATUS.READY,
        NODE_STATUS.RETIRED,
        NODE_STATUS.BROKEN,
        NODE_STATUS.ENTERING_RESCUE_MODE,
    ],
    NODE_STATUS.COMMISSIONING: [
        NODE_STATUS.FAILED_COMMISSIONING,
        NODE_STATUS.READY,
        NODE_STATUS.RETIRED,
        NODE_STATUS.MISSING,
        NODE_STATUS.NEW,
        NODE_STATUS.BROKEN,
        NODE_STATUS.TESTING,
    ],
    NODE_STATUS.FAILED_COMMISSIONING: [
        NODE_STATUS.COMMISSIONING,
        NODE_STATUS.MISSING,
        NODE_STATUS.RETIRED,
        NODE_STATUS.BROKEN,
        NODE_STATUS.ENTERING_RESCUE_MODE,
        NODE_STATUS.TESTING,
    ],
    NODE_STATUS.READY: [
        NODE_STATUS.ENTERING_RESCUE_MODE,
        NODE_STATUS.COMMISSIONING,
        NODE_STATUS.ALLOCATED,
        NODE_STATUS.RESERVED,
        NODE_STATUS.RETIRED,
        NODE_STATUS.MISSING,
        NODE_STATUS.BROKEN,
        NODE_STATUS.TESTING,
    ],
    NODE_STATUS.RESERVED: [
        NODE_STATUS.READY,
        NODE_STATUS.ALLOCATED,
        NODE_STATUS.RETIRED,
        NODE_STATUS.MISSING,
        NODE_STATUS.BROKEN,
        NODE_STATUS.DISK_ERASING,
        NODE_STATUS.RELEASING,
        NODE_STATUS.ENTERING_RESCUE_MODE,
        NODE_STATUS.TESTING,
    ],
    NODE_STATUS.ALLOCATED: [
        NODE_STATUS.READY,
        NODE_STATUS.RETIRED,
        NODE_STATUS.MISSING,
        NODE_STATUS.BROKEN,
        NODE_STATUS.DEPLOYING,
        NODE_STATUS.DISK_ERASING,
        NODE_STATUS.RELEASING,
        NODE_STATUS.ENTERING_RESCUE_MODE,
        NODE_STATUS.TESTING,
    ],
    NODE_STATUS.RELEASING: [
        NODE_STATUS.NEW,
        NODE_STATUS.ALLOCATED,
        NODE_STATUS.RESERVED,
        NODE_STATUS.DEPLOYED,
        NODE_STATUS.READY,
        NODE_STATUS.BROKEN,
        NODE_STATUS.MISSING,
        NODE_STATUS.FAILED_DEPLOYMENT,
        NODE_STATUS.FAILED_DISK_ERASING,
        NODE_STATUS.FAILED_RELEASING,
    ],
    NODE_STATUS.DEPLOYING: [
        NODE_STATUS.ALLOCATED,
        NODE_STATUS.MISSING,
        NODE_STATUS.BROKEN,
        NODE_STATUS.FAILED_DEPLOYMENT,
        NODE_STATUS.DEPLOYED,
        NODE_STATUS.READY,
        NODE_STATUS.DISK_ERASING,
        NODE_STATUS.RELEASING,
    ],
    NODE_STATUS.FAILED_DEPLOYMENT: [
        NODE_STATUS.ALLOCATED,
        NODE_STATUS.MISSING,
        NODE_STATUS.BROKEN,
        NODE_STATUS.READY,
        NODE_STATUS.DISK_ERASING,
        NODE_STATUS.RELEASING,
        NODE_STATUS.ENTERING_RESCUE_MODE,
        NODE_STATUS.TESTING,
    ],
    NODE_STATUS.DEPLOYED: [
        NODE_STATUS.ALLOCATED,
        NODE_STATUS.MISSING,
        NODE_STATUS.BROKEN,
        NODE_STATUS.READY,
        NODE_STATUS.DISK_ERASING,
        NODE_STATUS.RELEASING,
        NODE_STATUS.ENTERING_RESCUE_MODE,
        NODE_STATUS.TESTING,
    ],
    NODE_STATUS.MISSING: [
        NODE_STATUS.NEW,
        NODE_STATUS.READY,
        NODE_STATUS.ALLOCATED,
        NODE_STATUS.COMMISSIONING,
        NODE_STATUS.BROKEN,
        NODE_STATUS.ENTERING_RESCUE_MODE,
        NODE_STATUS.TESTING,
    ],
    NODE_STATUS.RETIRED: [
        NODE_STATUS.NEW,
        NODE_STATUS.READY,
        NODE_STATUS.MISSING,
        NODE_STATUS.BROKEN,
        NODE_STATUS.ENTERING_RESCUE_MODE,
        NODE_STATUS.TESTING,
    ],
    NODE_STATUS.BROKEN: [
        NODE_STATUS.COMMISSIONING,
        NODE_STATUS.READY,
        NODE_STATUS.DISK_ERASING,
        NODE_STATUS.RELEASING,
        NODE_STATUS.ENTERING_RESCUE_MODE,
        NODE_STATUS.TESTING,
        NODE_STATUS.DEPLOYED,
    ],
    NODE_STATUS.FAILED_RELEASING: [
        NODE_STATUS.RELEASING,
        NODE_STATUS.DISK_ERASING,
        NODE_STATUS.READY,
        NODE_STATUS.BROKEN,
        NODE_STATUS.TESTING,
    ],
    NODE_STATUS.DISK_ERASING: [
        NODE_STATUS.BROKEN,
        NODE_STATUS.DISK_ERASING,
        NODE_STATUS.FAILED_DISK_ERASING,
        NODE_STATUS.READY,
        NODE_STATUS.RELEASING,
    ],
    NODE_STATUS.FAILED_DISK_ERASING: [
        NODE_STATUS.DISK_ERASING,
        NODE_STATUS.RELEASING,
        NODE_STATUS.BROKEN,
        NODE_STATUS.ALLOCATED,
        NODE_STATUS.READY,
        NODE_STATUS.ENTERING_RESCUE_MODE,
        NODE_STATUS.TESTING,
    ],
    NODE_STATUS.ENTERING_RESCUE_MODE: [
        NODE_STATUS.READY,
        NODE_STATUS.BROKEN,
        NODE_STATUS.DEPLOYED,
        NODE_STATUS.RESCUE_MODE,
        NODE_STATUS.FAILED_ENTERING_RESCUE_MODE,
        NODE_STATUS.EXITING_RESCUE_MODE,
    ],
    NODE_STATUS.FAILED_ENTERING_RESCUE_MODE: [
        NODE_STATUS.BROKEN,
        NODE_STATUS.ENTERING_RESCUE_MODE,
        NODE_STATUS.EXITING_RESCUE_MODE,
        NODE_STATUS.TESTING,
    ],
    NODE_STATUS.RESCUE_MODE: [
        NODE_STATUS.BROKEN,
        NODE_STATUS.EXITING_RESCUE_MODE,
        NODE_STATUS.TESTING,
    ],
    NODE_STATUS.EXITING_RESCUE_MODE: [
        NODE_STATUS.NEW,
        NODE_STATUS.BROKEN,
        NODE_STATUS.FAILED_COMMISSIONING,
        NODE_STATUS.READY,
        NODE_STATUS.RESERVED,
        NODE_STATUS.ALLOCATED,
        NODE_STATUS.FAILED_DEPLOYMENT,
        NODE_STATUS.DEPLOYED,
        NODE_STATUS.MISSING,
        NODE_STATUS.RETIRED,
        NODE_STATUS.FAILED_RELEASING,
        NODE_STATUS.FAILED_DISK_ERASING,
        NODE_STATUS.RESCUE_MODE,
        NODE_STATUS.FAILED_ENTERING_RESCUE_MODE,
        NODE_STATUS.FAILED_EXITING_RESCUE_MODE,
        NODE_STATUS.FAILED_TESTING,
    ],
    NODE_STATUS.FAILED_EXITING_RESCUE_MODE: [
        NODE_STATUS.BROKEN,
        NODE_STATUS.EXITING_RESCUE_MODE,
        NODE_STATUS.TESTING,
    ],
    NODE_STATUS.TESTING: [
        NODE_STATUS.BROKEN,
        NODE_STATUS.NEW,
        NODE_STATUS.READY,
        NODE_STATUS.RESERVED,
        NODE_STATUS.ALLOCATED,
        NODE_STATUS.DEPLOYED,
    ]
    + list(NODE_TESTING_RESET_READY_TRANSITIONS),
    NODE_STATUS.FAILED_TESTING: [
        NODE_STATUS.COMMISSIONING,
        NODE_STATUS.BROKEN,
        NODE_STATUS.MISSING,
        NODE_STATUS.ENTERING_RESCUE_MODE,
        NODE_STATUS.TESTING,
        NODE_STATUS.READY,
        NODE_STATUS.DEPLOYED,
    ],
}

# State transitions for when a node fails:
# Mapping between in-progress statuses and the corresponding failed
# statuses.
NODE_FAILURE_STATUS_TRANSITIONS = {
    k.value: v.value for k, v in NODE_FAILURE_STATUS_TRANSITION_MAP.items()
}

# State transitions that are monitored for timeouts for when a node
# fails:
# Mapping between in-progress statuses and the corresponding failed
# statuses.
NODE_FAILURE_MONITORED_STATUS_TRANSITIONS = {
    NODE_STATUS.COMMISSIONING: NODE_STATUS.FAILED_COMMISSIONING,
    NODE_STATUS.DEPLOYING: NODE_STATUS.FAILED_DEPLOYMENT,
    NODE_STATUS.RELEASING: NODE_STATUS.FAILED_RELEASING,
    NODE_STATUS.ENTERING_RESCUE_MODE: NODE_STATUS.FAILED_ENTERING_RESCUE_MODE,
    NODE_STATUS.EXITING_RESCUE_MODE: NODE_STATUS.FAILED_EXITING_RESCUE_MODE,
    NODE_STATUS.TESTING: NODE_STATUS.FAILED_TESTING,
}

# Hard coded timeouts. All other statuses uses the config option 'node_timeout'
NODE_FAILURE_MONITORED_STATUS_TIMEOUTS = {
    NODE_STATUS.RELEASING: 5,
    NODE_STATUS.EXITING_RESCUE_MODE: 5,
}

# Statuses that correspond to managed steps for which MAAS actively
# monitors that the status changes after a fixed period of time.
MONITORED_STATUSES = list(NODE_FAILURE_STATUS_TRANSITIONS.keys())

# Non-active statuses.
NON_MONITORED_STATUSES = set(map_enum(NODE_STATUS).values()).difference(
    set(MONITORED_STATUSES)
)


FAILED_STATUSES = list(NODE_FAILURE_STATUS_TRANSITIONS.values())

# Statuses that are like commissioning, in that we boot an
# an ephemeral environment of the latest LTS, run some scripts
# provided via user data, and report back success/fail status.
COMMISSIONING_LIKE_STATUSES = [
    NODE_STATUS.NEW,
    NODE_STATUS.COMMISSIONING,
    NODE_STATUS.DISK_ERASING,
    NODE_STATUS.ENTERING_RESCUE_MODE,
    NODE_STATUS.RELEASING,
    NODE_STATUS.RESCUE_MODE,
    NODE_STATUS.TESTING,
]

# Node state transitions that perform query actions. This is to keep the
# power state of the node up-to-date when transitions occur that do not
# perform a power action directly.
QUERY_TRANSITIONS = {
    None: [NODE_STATUS.NEW],
    NODE_STATUS.COMMISSIONING: [
        NODE_STATUS.FAILED_COMMISSIONING,
        NODE_STATUS.READY,
    ],
    NODE_STATUS.DEPLOYING: [
        NODE_STATUS.FAILED_DEPLOYMENT,
        NODE_STATUS.DEPLOYED,
    ],
    NODE_STATUS.DISK_ERASING: [NODE_STATUS.FAILED_DISK_ERASING],
    NODE_STATUS.TESTING: [
        NODE_STATUS.FAILED_TESTING,
        NODE_STATUS.READY,
        NODE_STATUS.RESERVED,
        NODE_STATUS.ALLOCATED,
        NODE_STATUS.DEPLOYED,
    ],
}


def get_failed_status(status):
    """Returns the failed status corresponding to the given status.

    If no corresponding failed status exists, return None.
    """
    return NODE_FAILURE_STATUS_TRANSITIONS.get(status)


def is_failed_status(status):
    """Returns if the status is a 'failed' status."""
    return status in FAILED_STATUSES


def get_node_timeout(status, node_timeout=None):
    """Returns the timeout for the given status in minutes."""
    if status in MONITORED_STATUSES:
        if status in NODE_FAILURE_MONITORED_STATUS_TIMEOUTS:
            return NODE_FAILURE_MONITORED_STATUS_TIMEOUTS[status]
        else:
            if node_timeout is None:
                return Config.objects.get_config("node_timeout")
            else:
                return node_timeout
    return None
