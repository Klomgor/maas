from enum import Enum


class PowerTypeEnum(str, Enum):
    AMT = "amt"
    APC = "apc"
    DLI = "dli"
    EATON = "eaton"
    HMC = "hmc"
    HMCZ = "hmcz"
    IPMI = "ipmi"
    LXD = "lxd"
    MANUAL = "manual"
    MOONSHOT = "moonshot"
    MSCM = "mscm"
    MICROSOFT_OCS = "msftocs"
    OPENBMC = "openbmc"
    PROXMOX = "proxmox"
    RARITAN = "raritan"
    RECS = "recs_box"
    REDFISH = "redfish"
    SEAMICRO = "sm15k"
    UCSM = "ucsm"
    VIRSH = "virsh"
    VMWARE = "vmware"
    WEBHOOK = "webhook"
    WEDGE = "wedge"

    def __str__(self):
        return str(self.value)
