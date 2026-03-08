#!/usr/bin/env python3
"""
Congressional Trades Scanner
Scrapes House and Senate STOCK Act Periodic Transaction Reports (PTRs),
parses trade data from PDFs using pdfminer, caches results for 24 hours,
and returns structured trade signals.

Data Sources:
  House PTR ZIP:  https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2026FD.ZIP
  House PDFs:     https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/{DocID}.pdf

Notable politicians whose trades carry extra weight for signal scoring:
  Pelosi, Nancy | Pelosi, Paul | Tuberville, Tommy | Burr, Richard
"""

import json
import logging
import re
import tempfile
import zipfile
import io
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# Notable politicians that boost signal strength
NOTABLE_POLITICIANS = {
    "pelosi",
    "tuberville",
    "burr",
    "loeffler",
    "inhofe",
    "perdue",
    "collins",
}

# Amount-string to (min, max) mapping (approximate)
AMOUNT_RANGE_MAP = {
    "1,001 - $15,000": (1001, 15000),
    "15,001 - $50,000": (15001, 50000),
    "50,001 - $100,000": (50001, 100000),
    "100,001 - $250,000": (100001, 250000),
    "250,001 - $500,000": (250001, 500000),
    "500,001 - $1,000,000": (500001, 1000000),
    "1,000,001 - $5,000,000": (1000001, 5000000),
    "5,000,001 - $25,000,000": (5000001, 25000000),
    "25,000,001 - $50,000,000": (25000001, 50000000),
    "50,000,001 +": (50000001, 100000000),
}


def _parse_amount(amount_str: str):
    """Parse '$X,XXX - $Y,YYY' or similar into (min, max) ints."""
    try:
        nums = re.findall(r"[\d,]+", amount_str.replace("$", ""))
        nums = [int(n.replace(",", "")) for n in nums if n.replace(",", "").isdigit()]
        if len(nums) >= 2:
            return nums[0], nums[1]
        elif len(nums) == 1:
            return nums[0], nums[0]
    except Exception:
        pass
    return 0, 0


def _extract_trades_from_pdf_text(text: str, politician: str, filing_date: str, house: str) -> List[Dict]:
    """
    Parse raw pdfminer text to extract individual trades.

    Expected sample lines (null bytes stripped, whitespace collapsed):
      SPAlphabet Inc. - Class A CommonStock (GOOGL) [ST]P01/16/202601/16/2026$500,001 -$1,000,000
      SPAmazon.com, Inc. - Common Stock(AMZN) [OP]P12/30/202512/30/2025$100,001 -$250,000
      SPApple Inc. - Common Stock (AAPL)[ST]S (partial)12/24/202512/24/2025$5,000,001 -$25,000,000
    """
    trades = []
    if not text:
        return trades

    # Strip null bytes and normalise whitespace
    text = text.replace("\x00", " ")
    text = re.sub(r" {2,}", " ", text)

    # Each candidate block contains a ticker in parens followed by [ST|OP|OT]
    # We scan the text line-by-line for matching patterns
    lines = text.split("\n")
    full_text = " ".join(lines)

    # Find all ticker occurrences: (AAPL) [ST]  or (GOOGL)[OP]
    ticker_re = re.compile(r"\(([A-Z]{1,5})\)\s*\[(?:ST|OP|OT)\]")

    # Split full text at each ticker match so we can extract context
    segments = ticker_re.split(full_text)
    # segments alternates: [pre_text, ticker, bracket_type, pre_text, ticker, bracket_type, ...]
    # Actually re.split with a group gives: [before0, group1, group2, before1, ...]
    # But our pattern has two groups: ticker and [ST|OP|OT] type.
    # Use finditer instead for safety.

    for m in ticker_re.finditer(full_text):
        ticker = m.group(1)
        bracket_type = m.group(0)  # full match e.g. "(AAPL) [ST]"
        asset_type = "option" if "[OP]" in bracket_type else "stock"

        # Context window after the ticker match
        ctx_start = m.end()
        ctx = full_text[ctx_start: ctx_start + 200]

        # Transaction type: P = purchase, S = sale
        tx_m = re.search(r"\b(P|S)\b", ctx)
        if not tx_m:
            continue
        tx_code = tx_m.group(1)
        transaction_type = "purchase" if tx_code == "P" else "sale"

        # Date: first MM/DD/YYYY after the transaction code
        date_m = re.search(r"(\d{2}/\d{2}/\d{4})", ctx)
        trade_date = ""
        if date_m:
            try:
                trade_date = datetime.strptime(date_m.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
            except ValueError:
                trade_date = filing_date

        # Amount: $X,XXX - $Y,YYY or $X,XXX -$Y,YYY
        amt_m = re.search(r"\$[\d,]+\s*-\s*\$[\d,]+", ctx)
        amount_min, amount_max = 0, 0
        amount_str = ""
        if amt_m:
            amount_str = amt_m.group(0)
            amount_min, amount_max = _parse_amount(amount_str)

        trades.append({
            "symbol": ticker,
            "politician": politician,
            "house": house,
            "transaction_type": transaction_type,
            "amount_min": amount_min,
            "amount_max": amount_max,
            "trade_date": trade_date or filing_date,
            "filing_date": filing_date,
            "asset_type": asset_type,
            "description": f"{transaction_type.capitalize()} by {politician} ({amount_str})",
        })

    return trades


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Use pdfminer.six to extract text from PDF bytes."""
    try:
        from pdfminer.high_level import extract_text_to_fp
        from pdfminer.layout import LAParams
        import io as _io
        output = _io.StringIO()
        with _io.BytesIO(pdf_bytes) as pdf_fp:
            extract_text_to_fp(pdf_fp, output, laparams=LAParams())
        return output.getvalue()
    except ImportError:
        logger.warning("pdfminer.six not installed — cannot parse PDFs")
        return ""
    except Exception as e:
        logger.warning("PDF extraction failed: %s", e)
        return ""


class CongressionalTradesScanner:
    """Scrapes House PTR filings and returns structured trade signals."""

    HOUSE_ZIP_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.ZIP"
    HOUSE_PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
    CACHE_TTL_HOURS = 24
    MAX_PDFS_PER_FETCH = 20

    def __init__(self, cache_dir: Path = None):
        if cache_dir is None:
            cache_dir = Path(__file__).resolve().parent.parent.parent / "data"
        self.cache_dir = Path(cache_dir)
        self.cache_file = self.cache_dir / "congressional_cache.json"

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def get_trades(self, days_back: int = 30) -> List[Dict]:
        """Return cached or fresh list of congressional trades.

        Each trade dict:
            symbol, politician, house, transaction_type,
            amount_min, amount_max, trade_date, filing_date,
            asset_type, description
        """
        cached = self._load_cache()
        if cached is not None:
            logger.info("Congressional trades: using cached data (%d trades)", len(cached))
            return cached

        logger.info("Congressional trades: cache miss — fetching fresh data")
        trades = self._fetch_house_trades(days_back=days_back)
        self._save_cache(trades)
        return trades

    def get_signals_for_symbols(self, symbols: List[str]) -> Dict[str, Dict]:
        """Aggregate congressional trade signals for a list of symbols.

        Returns:
            {
                'AAPL': {
                    'net_sentiment': float,   # -1..+1
                    'purchase_count': int,
                    'sale_count': int,
                    'notable_politicians': list[str],
                    'last_trade_date': str,
                    'total_min_value': int,
                    'signal': 'bullish' | 'bearish' | 'neutral',
                }
            }
        """
        if not symbols:
            return {}

        all_trades = self.get_trades()
        symbol_set = {s.upper() for s in symbols}
        result: Dict[str, Dict] = {}

        for sym in symbol_set:
            sym_trades = [t for t in all_trades if t.get("symbol", "").upper() == sym]
            if not sym_trades:
                continue

            purchases = [t for t in sym_trades if t["transaction_type"] == "purchase"]
            sales = [t for t in sym_trades if t["transaction_type"] == "sale"]

            total = len(sym_trades)
            net_score = (len(purchases) - len(sales)) / total if total else 0.0

            # Identify notable politicians
            notable = []
            for t in sym_trades:
                pol_lower = t.get("politician", "").lower()
                if any(k in pol_lower for k in NOTABLE_POLITICIANS):
                    pol = t["politician"]
                    if pol not in notable:
                        notable.append(pol)

            # Most recent trade date
            dates = [t["trade_date"] for t in sym_trades if t.get("trade_date")]
            last_date = max(dates) if dates else ""

            # Total minimum value across all trades
            total_min_val = sum(t.get("amount_min", 0) for t in sym_trades)

            # Signal classification
            if net_score >= 0.25:
                signal = "bullish"
            elif net_score <= -0.25:
                signal = "bearish"
            else:
                signal = "neutral"

            result[sym] = {
                "net_sentiment": round(net_score, 3),
                "purchase_count": len(purchases),
                "sale_count": len(sales),
                "notable_politicians": notable,
                "last_trade_date": last_date,
                "total_min_value": total_min_val,
                "signal": signal,
            }

        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Internal: fetching
    # ──────────────────────────────────────────────────────────────────────────

    def _fetch_house_trades(self, days_back: int = 30) -> List[Dict]:
        """Download House PTR XML ZIP, parse member list, fetch + parse PDFs."""
        try:
            import requests as _req
        except ImportError:
            logger.warning("requests not available — cannot fetch congressional trades")
            return []

        year = datetime.now().year
        zip_url = self.HOUSE_ZIP_URL.format(year=year)
        trades: List[Dict] = []

        # --- Download ZIP ---
        try:
            logger.info("Downloading House PTR ZIP: %s", zip_url)
            resp = _req.get(zip_url, timeout=30)
            resp.raise_for_status()
            zip_bytes = resp.content
        except Exception as e:
            logger.warning("House PTR ZIP download failed: %s", e)
            return []

        # --- Parse XML to get recent PTR filings ---
        try:
            members = self._parse_member_xml(zip_bytes, days_back=days_back)
        except Exception as e:
            logger.warning("House PTR XML parse failed: %s", e)
            return []

        if not members:
            logger.info("No recent House PTR filings found in last %d days", days_back)
            return []

        logger.info("Found %d recent House PTR filings (capped at %d)", len(members), self.MAX_PDFS_PER_FETCH)
        members = members[: self.MAX_PDFS_PER_FETCH]

        # --- Download and parse each PDF ---
        for member in members:
            doc_id = member.get("doc_id", "")
            politician = member.get("politician", "Unknown")
            filing_date = member.get("filing_date", "")

            if not doc_id:
                continue

            try:
                pdf_url = self.HOUSE_PDF_URL.format(year=year, doc_id=doc_id)
                pdf_resp = _req.get(pdf_url, timeout=20)
                pdf_resp.raise_for_status()
                pdf_bytes = pdf_resp.content
            except Exception as e:
                logger.warning("PDF download failed for %s (%s): %s", politician, doc_id, e)
                continue

            try:
                text = _extract_text_from_pdf(pdf_bytes)
                pdf_trades = _extract_trades_from_pdf_text(
                    text, politician=politician, filing_date=filing_date, house="house"
                )
                logger.debug("  %s: %d trades parsed", politician, len(pdf_trades))
                trades.extend(pdf_trades)
            except Exception as e:
                logger.warning("PDF parse failed for %s: %s", politician, e)
                continue

        logger.info("Congressional trades fetched: %d total trades", len(trades))
        return trades

    def _parse_member_xml(self, zip_bytes: bytes, days_back: int = 30) -> List[Dict]:
        """Parse the XML inside the House PTR ZIP.

        Returns list of:
            { 'politician': 'Last, First', 'doc_id': '20033725', 'filing_date': '2026-01-16' }
        Only PTR filings (FilingType=P) within days_back window are returned,
        sorted newest first.
        """
        try:
            import xml.etree.ElementTree as ET
        except ImportError:
            logger.warning("xml.etree.ElementTree unavailable")
            return []

        cutoff = datetime.now() - timedelta(days=days_back)
        members = []

        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
                if not xml_names:
                    logger.warning("No XML file found in House PTR ZIP")
                    return []
                xml_content = zf.read(xml_names[0])
        except Exception as e:
            logger.warning("Failed to read ZIP: %s", e)
            return []

        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            logger.warning("XML parse error: %s", e)
            return []

        # Handle potential namespace
        ns_match = re.match(r"\{[^}]+\}", root.tag)
        ns = ns_match.group(0) if ns_match else ""

        for member_el in root.iter(f"{ns}Member"):
            filing_type = (member_el.findtext(f"{ns}FilingType") or "").strip().upper()
            if filing_type != "P":
                continue  # Only Periodic Transaction Reports

            last = (member_el.findtext(f"{ns}Last") or "").strip()
            first = (member_el.findtext(f"{ns}First") or "").strip()
            doc_id = (member_el.findtext(f"{ns}DocID") or "").strip()
            filing_date_raw = (member_el.findtext(f"{ns}FilingDate") or "").strip()

            if not doc_id:
                continue

            # Parse filing date
            filing_date = ""
            for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
                try:
                    dt = datetime.strptime(filing_date_raw, fmt)
                    if dt < cutoff:
                        break  # too old
                    filing_date = dt.strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue

            if not filing_date:
                continue  # outside window or unparseable

            politician = f"{last}, {first}".strip(", ")
            members.append({
                "politician": politician,
                "doc_id": doc_id,
                "filing_date": filing_date,
            })

        # Sort newest first
        members.sort(key=lambda x: x["filing_date"], reverse=True)
        return members

    # ──────────────────────────────────────────────────────────────────────────
    # Internal: cache
    # ──────────────────────────────────────────────────────────────────────────

    def _load_cache(self) -> Optional[List[Dict]]:
        """Return cached trades if cache is < 24 h old, else None."""
        try:
            if not self.cache_file.exists():
                return None
            with open(self.cache_file) as f:
                data = json.load(f)
            cached_at_str = data.get("cached_at", "")
            if not cached_at_str:
                return None
            cached_at = datetime.fromisoformat(cached_at_str)
            age_hours = (datetime.now() - cached_at).total_seconds() / 3600
            if age_hours >= self.CACHE_TTL_HOURS:
                return None
            return data.get("trades", [])
        except Exception as e:
            logger.debug("Cache load failed: %s", e)
            return None

    def _save_cache(self, trades: List[Dict]):
        """Write trades to cache file."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_data = {
                "trades": trades,
                "cached_at": datetime.now().isoformat(),
                "trade_count": len(trades),
            }
            with open(self.cache_file, "w") as f:
                json.dump(cache_data, f, indent=2)
            logger.debug("Congressional cache saved: %d trades", len(trades))
        except Exception as e:
            logger.warning("Cache save failed: %s", e)
