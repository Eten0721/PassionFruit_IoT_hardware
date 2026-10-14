"""Generate farmer notification previews locally; never send them."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from fruit_app.notification import generate_notification


class Command(BaseCommand):
    help = '從指定 result.json 產生加工果通知文字、JPEG 合成圖與預覽圖。'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('result_json', type=Path)
        parser.add_argument('--output-dir', type=Path,
                            help='輸出目錄；預設為該果實結果目錄。')
        parser.add_argument('--font-path', type=Path,
                            help='中文字型；預設 C:/Windows/Fonts/msjh.ttc。')

    def handle(self, *args, **options):
        try:
            result = generate_notification(
                options['result_json'], output_dir=options['output_dir'],
                font_path=options['font_path'],
            )
        except (OSError, ValueError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
