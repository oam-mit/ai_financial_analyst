"""
One-off management command to backfill correct filing dates from SEC file headers.
Run with: uv run python manage.py fix_filing_dates
"""
import re
from datetime import datetime
from django.core.management.base import BaseCommand
from investing.models import SECFiling


class Command(BaseCommand):
    help = 'Backfill correct filing dates from SEC full-submission.txt headers'

    def handle(self, *args, **options):
        filings = SECFiling.objects.all()
        self.stdout.write(f'Checking {filings.count()} filings...')
        updated = 0
        skipped = 0

        for filing in filings:
            parsed_date = self._parse_filing_date(filing.content_path)
            if parsed_date and filing.filing_date != parsed_date:
                self.stdout.write(
                    f'  {filing.stock.symbol} [{filing.accession_number}]: '
                    f'{filing.filing_date} → {parsed_date}'
                )
                filing.filing_date = parsed_date
                filing.save(update_fields=['filing_date'])
                updated += 1
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f'Done. Updated {updated} filings, {skipped} already correct or unreadable.'
        ))

    def _parse_filing_date(self, filing_path):
        try:
            with open(filing_path, 'r', encoding='utf-8', errors='ignore') as f:
                header = f.read(2000)
            match = re.search(r'FILED AS OF DATE:\s+(\d{8})', header)
            if match:
                return datetime.strptime(match.group(1), '%Y%m%d').date()
        except Exception:
            pass
        return None
