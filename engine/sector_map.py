"""
sector_map.py - Symbol-to-Sector Mapping for Portfolio Risk Management

Maps 200+ common symbols to sectors for concentration risk monitoring.
Includes sector ETF mapping for hedging and correlation analysis.

Author: Risk Fortress System
Date: 2026-02-17
"""

import logging

logger = logging.getLogger(__name__)

# Comprehensive symbol-to-sector mapping (200+ symbols)
SYMBOL_TO_SECTOR = {
    # Technology (Big Tech, Software, Hardware, Semiconductors)
    'AAPL': 'technology', 'MSFT': 'technology', 'GOOGL': 'technology', 'GOOG': 'technology',
    'AMZN': 'technology', 'META': 'technology', 'NVDA': 'technology', 'AMD': 'technology',
    'INTC': 'technology', 'CRM': 'technology', 'ORCL': 'technology', 'ADBE': 'technology',
    'CSCO': 'technology', 'AVGO': 'technology', 'QCOM': 'technology', 'TXN': 'technology',
    'AMAT': 'technology', 'LRCX': 'technology', 'KLAC': 'technology', 'MU': 'technology',
    'NXPI': 'technology', 'MRVL': 'technology', 'SNPS': 'technology', 'CDNS': 'technology',
    'NOW': 'technology', 'PANW': 'technology', 'CRWD': 'technology', 'ZS': 'technology',
    'DDOG': 'technology', 'NET': 'technology', 'SNOW': 'technology', 'TEAM': 'technology',
    'UBER': 'technology', 'LYFT': 'technology', 'ABNB': 'technology', 'DASH': 'technology',
    'SQ': 'technology', 'PYPL': 'technology', 'SHOP': 'technology', 'TWLO': 'technology',
    'OKTA': 'technology', 'ZM': 'technology', 'DOCU': 'technology', 'WDAY': 'technology',
    'SPLK': 'technology', 'FTNT': 'technology', 'CHKP': 'technology', 'IBM': 'technology',
    'HPQ': 'technology', 'HPE': 'technology', 'DELL': 'technology', 'WDC': 'technology',
    'STX': 'technology', 'NTAP': 'technology', 'PSTG': 'technology',
    
    # Healthcare (Pharma, Biotech, Medical Devices, Health Services)
    'JNJ': 'healthcare', 'PFE': 'healthcare', 'UNH': 'healthcare', 'ABBV': 'healthcare',
    'MRK': 'healthcare', 'TMO': 'healthcare', 'ABT': 'healthcare', 'LLY': 'healthcare',
    'BMY': 'healthcare', 'AMGN': 'healthcare', 'GILD': 'healthcare', 'CVS': 'healthcare',
    'CI': 'healthcare', 'ISRG': 'healthcare', 'VRTX': 'healthcare', 'REGN': 'healthcare',
    'HUM': 'healthcare', 'BIIB': 'healthcare', 'ILMN': 'healthcare', 'MRNA': 'healthcare',
    'BNTX': 'healthcare', 'ZTS': 'healthcare', 'SYK': 'healthcare', 'BSX': 'healthcare',
    'MDT': 'healthcare', 'EW': 'healthcare', 'DXCM': 'healthcare', 'ALGN': 'healthcare',
    'IQV': 'healthcare', 'CNC': 'healthcare', 'MOH': 'healthcare',
    
    # Finance (Banks, Investment Banks, Asset Managers, Fintech)
    'JPM': 'finance', 'BAC': 'finance', 'WFC': 'finance', 'GS': 'finance',
    'MS': 'finance', 'C': 'finance', 'BLK': 'finance', 'SCHW': 'finance',
    'AXP': 'finance', 'USB': 'finance', 'PNC': 'finance', 'TFC': 'finance',
    'BK': 'finance', 'STT': 'finance', 'NTRS': 'finance', 'RF': 'finance',
    'CFG': 'finance', 'KEY': 'finance', 'FITB': 'finance', 'HBAN': 'finance',
    'V': 'finance', 'MA': 'finance', 'COF': 'finance', 'DFS': 'finance',
    'SYF': 'finance', 'TROW': 'finance', 'BEN': 'finance', 'IVZ': 'finance',
    
    # Energy (Oil & Gas, Refiners, Services)
    'XOM': 'energy', 'CVX': 'energy', 'COP': 'energy', 'SLB': 'energy',
    'EOG': 'energy', 'PSX': 'energy', 'VLO': 'energy', 'MPC': 'energy',
    'OXY': 'energy', 'HAL': 'energy', 'BKR': 'energy', 'DVN': 'energy',
    'FANG': 'energy', 'MRO': 'energy', 'APA': 'energy', 'HES': 'energy',
    'KMI': 'energy', 'WMB': 'energy', 'OKE': 'energy', 'LNG': 'energy',
    
    # Consumer (Retail, Consumer Goods, Restaurants)
    'WMT': 'consumer', 'COST': 'consumer', 'TGT': 'consumer', 'HD': 'consumer',
    'LOW': 'consumer', 'NKE': 'consumer', 'SBUX': 'consumer', 'MCD': 'consumer',
    'PG': 'consumer', 'KO': 'consumer', 'PEP': 'consumer', 'PM': 'consumer',
    'MO': 'consumer', 'MDLZ': 'consumer', 'CL': 'consumer', 'KMB': 'consumer',
    'GIS': 'consumer', 'K': 'consumer', 'HSY': 'consumer', 'CPB': 'consumer',
    'DG': 'consumer', 'DLTR': 'consumer', 'ROST': 'consumer', 'TJX': 'consumer',
    'YUM': 'consumer', 'CMG': 'consumer', 'QSR': 'consumer', 'DPZ': 'consumer',
    
    # Industrials (Aerospace, Defense, Manufacturing, Logistics)
    'CAT': 'industrials', 'DE': 'industrials', 'UPS': 'industrials', 'FDX': 'industrials',
    'HON': 'industrials', 'GE': 'industrials', 'BA': 'industrials', 'LMT': 'industrials',
    'RTX': 'industrials', 'NOC': 'industrials', 'GD': 'industrials', 'LHX': 'industrials',
    'MMM': 'industrials', 'EMR': 'industrials', 'ETN': 'industrials', 'ITW': 'industrials',
    'PH': 'industrials', 'ROK': 'industrials', 'CMI': 'industrials', 'PCAR': 'industrials',
    'JCI': 'industrials', 'CARR': 'industrials', 'OTIS': 'industrials', 'WM': 'industrials',
    'RSG': 'industrials', 'URI': 'industrials', 'CSX': 'industrials', 'UNP': 'industrials',
    'NSC': 'industrials',
    
    # Materials (Chemicals, Metals, Mining)
    'LIN': 'materials', 'APD': 'materials', 'ECL': 'materials', 'NEM': 'materials',
    'FCX': 'materials', 'SHW': 'materials', 'DD': 'materials', 'DOW': 'materials',
    'PPG': 'materials', 'NUE': 'materials', 'VMC': 'materials', 'MLM': 'materials',
    'GOLD': 'materials', 'AA': 'materials', 'CF': 'materials', 'MOS': 'materials',
    
    # Utilities (Electric, Gas, Water)
    'NEE': 'utilities', 'DUK': 'utilities', 'SO': 'utilities', 'D': 'utilities',
    'AEP': 'utilities', 'EXC': 'utilities', 'SRE': 'utilities', 'PEG': 'utilities',
    'XEL': 'utilities', 'ED': 'utilities', 'ES': 'utilities', 'AWK': 'utilities',
    'WEC': 'utilities', 'DTE': 'utilities', 'PPL': 'utilities', 'FE': 'utilities',
    
    # Real Estate (REITs)
    'AMT': 'real_estate', 'PLD': 'real_estate', 'CCI': 'real_estate', 'EQIX': 'real_estate',
    'SPG': 'real_estate', 'PSA': 'real_estate', 'DLR': 'real_estate', 'O': 'real_estate',
    'WELL': 'real_estate', 'AVB': 'real_estate', 'EQR': 'real_estate', 'VTR': 'real_estate',
    'ARE': 'real_estate', 'MAA': 'real_estate', 'INVH': 'real_estate', 'ESS': 'real_estate',
    
    # Communication Services (Media, Telecom, Entertainment)
    'NFLX': 'communication', 'DIS': 'communication', 'CMCSA': 'communication', 'T': 'communication',
    'VZ': 'communication', 'TMUS': 'communication', 'CHTR': 'communication', 'EA': 'communication',
    'ATVI': 'communication', 'TTWO': 'communication', 'RBLX': 'communication', 'MTCH': 'communication',
    'FOXA': 'communication', 'FOX': 'communication', 'PARA': 'communication', 'WBD': 'communication',
    'OMC': 'communication', 'IPG': 'communication',
    
    # Meme Stocks (High-volatility retail favorites)
    'GME': 'meme', 'AMC': 'meme', 'BBBY': 'meme', 'BB': 'meme',
    'CLOV': 'meme', 'WISH': 'meme', 'PLTR': 'meme', 'SOFI': 'meme',
    'HOOD': 'meme', 'RIVN': 'meme', 'LCID': 'meme', 'TSLA': 'meme',
    
    # Crypto-Related (Exchanges, Miners, Proxy Plays)
    'COIN': 'crypto_related', 'MSTR': 'crypto_related', 'RIOT': 'crypto_related',
    'MARA': 'crypto_related', 'BITO': 'crypto_related', 'GBTC': 'crypto_related',
    'HUT': 'crypto_related', 'BITF': 'crypto_related', 'CLSK': 'crypto_related',
    
    # Equity ETFs
    'SPY': 'etf', 'QQQ': 'etf', 'VOO': 'etf', 'VTI': 'etf',
    'IVV': 'etf', 'IWM': 'etf', 'DIA': 'etf', 'VEA': 'etf',
    'VWO': 'etf', 'EFA': 'etf', 'EEM': 'etf', 'VUG': 'etf',
    'VTV': 'etf', 'VO': 'etf', 'VB': 'etf', 'SCHB': 'etf',
    'SCHX': 'etf', 'SCHA': 'etf', 'SCHM': 'etf', 'XLK': 'etf',
    'XLF': 'etf', 'XLE': 'etf', 'XLV': 'etf', 'XLY': 'etf',
    'XLP': 'etf', 'XLI': 'etf', 'XLB': 'etf', 'XLU': 'etf',
    'XLRE': 'etf', 'XLC': 'etf',
    
    # Bond ETFs
    'AGG': 'bond_etf', 'BND': 'bond_etf', 'LQD': 'bond_etf', 'TLT': 'bond_etf',
    'GOVT': 'bond_etf', 'MUB': 'bond_etf', 'SHY': 'bond_etf', 'IEF': 'bond_etf',
    'TIP': 'bond_etf', 'HYG': 'bond_etf', 'JNK': 'bond_etf', 'EMB': 'bond_etf',
    'VCIT': 'bond_etf', 'VCSH': 'bond_etf', 'BNDX': 'bond_etf',
    
    # Commodity ETFs
    'GLD': 'commodity_etf', 'IAU': 'commodity_etf', 'SGOL': 'commodity_etf',
    'SLV': 'commodity_etf', 'USO': 'commodity_etf', 'UNG': 'commodity_etf',
    'DBA': 'commodity_etf', 'DBC': 'commodity_etf', 'PDBC': 'commodity_etf',
}

# Sector-to-representative ETF mapping (for hedging and correlation analysis)
SECTOR_TO_ETF = {
    'technology': 'XLK',
    'healthcare': 'XLV',
    'finance': 'XLF',
    'energy': 'XLE',
    'consumer': 'XLY',
    'industrials': 'XLI',
    'materials': 'XLB',
    'utilities': 'XLU',
    'real_estate': 'XLRE',
    'communication': 'XLC',
    'meme': 'SPY',  # No meme ETF, use market proxy
    'crypto_related': 'BITO',
    'etf': 'SPY',
    'bond_etf': 'AGG',
    'commodity_etf': 'GLD',
    'other': 'SPY',
}


def get_sector(symbol: str) -> str:
    """
    Get the sector for a given symbol.
    
    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL', 'GME')
    
    Returns:
        Sector string (e.g., 'technology', 'meme', 'other')
    """
    if not symbol:
        logger.warning("get_sector called with empty symbol")
        return 'other'
    
    symbol = symbol.upper().strip()
    sector = SYMBOL_TO_SECTOR.get(symbol, 'other')
    
    if sector == 'other':
        logger.debug(f"Symbol {symbol} not found in sector map, returning 'other'")
    
    return sector


def get_sector_etf(sector: str) -> str:
    """
    Get the representative ETF for a given sector.
    Useful for correlation analysis and hedging.
    
    Args:
        sector: Sector name (e.g., 'technology', 'healthcare')
    
    Returns:
        ETF ticker symbol (e.g., 'XLK', 'XLV')
    """
    if not sector:
        logger.warning("get_sector_etf called with empty sector")
        return 'SPY'
    
    sector = sector.lower().strip()
    etf = SECTOR_TO_ETF.get(sector, 'SPY')
    
    return etf


def get_all_sectors() -> list:
    """
    Get list of all defined sectors.
    
    Returns:
        List of sector names
    """
    return list(set(SYMBOL_TO_SECTOR.values()))


def get_symbols_in_sector(sector: str) -> list:
    """
    Get all symbols in a given sector.
    
    Args:
        sector: Sector name (e.g., 'technology')
    
    Returns:
        List of ticker symbols in that sector
    """
    sector = sector.lower().strip()
    return [symbol for symbol, s in SYMBOL_TO_SECTOR.items() if s == sector]


def is_high_risk_sector(sector: str) -> bool:
    """
    Identify high-volatility sectors that require stricter position limits.
    
    Args:
        sector: Sector name
    
    Returns:
        True if sector is considered high-risk
    """
    high_risk = ['meme', 'crypto_related']
    return sector.lower() in high_risk


if __name__ == '__main__':
    # Test the mapping
    logging.basicConfig(level=logging.INFO)
    
    test_symbols = ['AAPL', 'GME', 'TSLA', 'SPY', 'UNKNOWN', 'COIN']
    print("Symbol-to-Sector Test:")
    for symbol in test_symbols:
        sector = get_sector(symbol)
        etf = get_sector_etf(sector)
        print(f"  {symbol:8} → {sector:15} → {etf}")
    
    print(f"\nTotal sectors: {len(get_all_sectors())}")
    print(f"Total symbols mapped: {len(SYMBOL_TO_SECTOR)}")
    print(f"Meme stocks: {get_symbols_in_sector('meme')}")
