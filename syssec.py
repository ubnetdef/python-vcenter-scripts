import atexit
import random
import re

import click
from pyVim.connect import Disconnect, SmartConnect, SmartConnectNoSSL
from pyVmomi import vim


LOGIN_DOMAIN = 'ad'
SYSSEC_FOLDER_NAME = 'SysSec'
SYSSEC_ROLE_NAME = 'Systems Security'

# TODO: re-evaluate having these as global
datacenter = None
service_instance = None
syssec_folder = None


@click.group()
@click.option('--host', default='cdr-vcenter1.cse.buffalo.edu')
@click.option('--port', default=443)
@click.option('--ssl/--no-ssl', default=False)
@click.option('--user', required=True, prompt=True)
@click.password_option(confirmation_prompt=False)
@click.option('--datacenter', 'datacenter_name', default='UBNetDef')
def cli(host, port, ssl, user, password, datacenter_name):
    global datacenter, service_instance, syssec_folder

    smart_connect_class = SmartConnect if ssl else SmartConnectNoSSL
    service_instance = smart_connect_class(
        host=host,
        port=port,
        user=user,
        pwd=password,
    )
    if not service_instance:
        raise RuntimeError('Could not log in')
    atexit.register(Disconnect, service_instance)

    datacenters = service_instance.content.rootFolder.childEntity
    try:
        datacenter = [dc for dc in datacenters if dc.name == datacenter_name][0]
    except IndexError:
        raise RuntimeError('Datacenter {!r} not found.'.format(datacenter_name))

    folders = datacenter.vmFolder.childEntity
    try:
        syssec_folder = [f for f in folders if f.name == SYSSEC_FOLDER_NAME][0]
    except IndexError:
        raise RuntimeError('SysSec folder not found.')


@cli.command()
@click.argument('ubit_names_file')
def create_folders(ubit_names_file):
    """Create subfolders under the SysSec folder and assign permissions to them."""
    with open(ubit_names_file) as file:
        ubit_names = file.read().splitlines()
    
    authorization_manager = service_instance.content.authorizationManager
    roles = authorization_manager.roleList
    try:
        syssec_role = [r for r in roles if r.name == SYSSEC_ROLE_NAME][0]
    except IndexError:
        raise RuntimeError('Could not find SysSec role to assign permissions.')

    for num, ubit_name in enumerate(ubit_names):
        folder_name = '{:02d}: {}'.format(num + 1, ubit_name)
        folder = syssec_folder.CreateFolder(folder_name)
        permission = vim.AuthorizationManager.Permission(
            group=False,
            principal='{}@{}'.format(ubit_name, LOGIN_DOMAIN),
            propagate=True,
            roleId=syssec_role.roleId,
        )
        authorization_manager.SetEntityPermissions(folder, [permission])


@cli.command()
@click.option('--template-name', required=True)
@click.option('--vm-name', required=True)
def deploy_vms(template_name, vm_name):
    try:
        template = next(_find_object(types=[vim.VirtualMachine], name=template_name))
    except StopIteration:
        raise RuntimeError('Could not find the template.')

    for folder in syssec_folder.childEntity:
        # Get first number from folder name, if any.
        num = re.search(r'\d+', folder.name)
        num = num.group(0) if num else ''

        datastore = _get_datastore_cluster()
        compute_resource_pool = _get_compute_cluster().resourcePool
        relocate_spec = vim.vm.RelocateSpec()
        relocate_spec.datastore = datastore
        relocate_spec.pool = compute_resource_pool

        clone_spec = vim.vm.CloneSpec(
            location=relocate_spec,
            powerOn=False,
            template=False,
        )

        template.Clone(folder, vm_name.format(num), clone_spec)


def _find_object(*_, container=None, types=None, name=None):
    view_manager = service_instance.content.viewManager
    container_view = view_manager.CreateContainerView(
        container=container or datacenter.vmFolder,
        type=types or [],
        recursive=True,
    )
    for reference in container_view.view:
        if name is None or reference.name == name:
            yield reference


def _get_compute_cluster():
    clusters = _find_object(
        container=service_instance.content.rootFolder,
        types=[vim.ClusterComputeResource],
        name='MAIN',
    )
    return next(clusters)


def _get_datastore_cluster():
    datastores = _find_object(
        container=service_instance.content.rootFolder,
        types=[vim.Datastore],
    )
    return random.choice([
        datastore
        for datastore in datastores
        if datastore.name in ('cdr-iscsi2', 'cdr-iscsi3')
    ])


if __name__ == '__main__':
    cli()
