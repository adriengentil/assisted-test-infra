import ipaddress
from pathlib import PurePath
from typing import Any, Final

from ansible.inventory.host import Host
from ansible.inventory.manager import InventoryManager
from ansible.parsing.dataloader import DataLoader

from assisted_test_infra.test_infra import BaseTerraformConfig
from assisted_test_infra.test_infra.tools.assets import LibvirtNetworkAssets
from consts import consts


class TerraformConfigHelper:
    """
    Update TerraformConfig object from CI machine inventory.
    The inventory file format must be compatible with the Ansible inventory format.
    For each host, these the following variables must be set:
    - libvirt_uri: connection string to libvirt,
        e.g: qemu+ssh://root@10.0.0.181/system?known_hosts_verify=ignore&keyfile=/root/.ssh/id_cluster
    - ipv4_network_prefix: IPv4 network prefix, the prefix must be strictly lower than /24, e.g.: 172.16.0.0/20
    - ipv6_network_prefix: IPv6 network prefix, the prefix must be strictly lower than /64, e.g.: fd1a:7c7b:f55e::/48
    """

    _DEFAULT_IPV4_SUBNET_LENGTH: Final[int] = 24
    _DEFAULT_IPV6_SUBNET_LENGTH: Final[int] = 64

    def __init__(self, inventory_file: str):
        dl = DataLoader()
        self._inventory = InventoryManager(loader=dl, sources=inventory_file)

    def update(self, config: BaseTerraformConfig, **kwargs) -> LibvirtNetworkAssets:
        """Create a TerraformConfig which targets a host matching the input criteria."""
        selected_host = self._search_host(**kwargs)
        return self._update_terraform_config_from_hostvars(config, selected_host)

    def _search_host(self, **kwargs) -> Host:
        hosts = self._inventory.get_hosts()
        if len(hosts) == 1:
            return hosts[0]

        for host in self._inventory.get_hosts():
            hostvars = host.get_vars()
            if all(k in hostvars for k in kwargs.keys()) and all(hostvars[k] == v for k, v in kwargs.items()):
                return host

        raise RuntimeError(f"No host found matching criteria: {kwargs}")

    def _update_terraform_config_from_hostvars(self, config: BaseTerraformConfig, host: Host) -> LibvirtNetworkAssets:
        hostvars = host.get_vars()

        # set libvirt_uri
        config.libvirt_uri = hostvars["libvirt_uri"]

        # Provide base asset to compute network settings
        ipv4_network_prefix = ipaddress.ip_network(hostvars["ipv4_network_prefix"])
        ipv4_available_subnets = list(
            ipv4_network_prefix.subnets(new_prefix=self._DEFAULT_IPV4_SUBNET_LENGTH)
        )  # split the network prefix into /24 networks

        ipv6_network_prefix = ipaddress.ip_network(hostvars["ipv6_network_prefix"])
        ipv6_available_subnets = list(
            ipv6_network_prefix.subnets(new_prefix=self._DEFAULT_IPV6_SUBNET_LENGTH)
        )  # split the network prefix into /24 networks

        base_asset = {}
        base_asset["machine_cidr"] = ipv4_available_subnets[0]  # aka primary-net
        base_asset["provisioning_cidr"] = ipv4_available_subnets[len(ipv4_available_subnets) // 2]  # aka secondary-net

        base_asset["machine_cidr6"] = ipv6_available_subnets[0]
        base_asset["provisioning_cidr6"] = ipv6_available_subnets[len(ipv6_available_subnets) // 2]

        base_asset["libvirt_network_if"] = consts.BaseAsset.NETWORK_IF
        base_asset["libvirt_secondary_network_if"] = consts.BaseAsset.SECONDARY_NETWORK_IF

        assets_file = PurePath(consts.TF_NETWORK_POOL_PATH)
        assets_file = assets_file.parent / f"{host}_net_asset.json"
        net_asset = LibvirtNetworkAssets(
            assets_file=str(assets_file), base_asset=base_asset, libvirt_uri=config.libvirt_uri
        )
        config.net_asset = net_asset.get()

        return net_asset
