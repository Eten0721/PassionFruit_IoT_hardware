"""Reset selection only in the HTTPS serving process, including autoreload."""
import threading

from sslserver.management.commands.runsslserver import Command as SSLCommand

from fruit_app import local_network
from fruit_app import notification_delivery
from fruit_app import views


class Command(SSLCommand):
    def inner_run(self, *args, **options):
        local_network.NETWORK.start_run(port=int(self.port))
        stop = threading.Event()
        monitor = threading.Thread(target=views.monitor_network_safety, args=(stop,),
                                   name='control-safety', daemon=True)
        notifier = threading.Thread(target=notification_delivery.run_worker,
                                    args=(views.notification_outbox, stop),
                                    name='line-notification', daemon=True)
        try:
            monitor.start()
            notifier.start()
            local_network.NETWORK.start_monitor()
            return super().inner_run(*args, **options)
        finally:
            stop.set()
            notification_delivery.wake()
            for worker in (monitor, notifier):
                if worker.ident is not None:
                    worker.join(timeout=2)
            local_network.NETWORK.start_run()
