"""Reset selection only in the HTTPS serving process, including autoreload."""
from sslserver.management.commands.runsslserver import Command as SSLCommand

from fruit_app import local_network


class Command(SSLCommand):
    def inner_run(self, *args, **options):
        local_network.NETWORK.start_run(port=int(self.port))
        try:
            local_network.NETWORK.start_monitor()
            return super().inner_run(*args, **options)
        finally:
            local_network.NETWORK.start_run()
