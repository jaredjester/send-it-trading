"""
FinBERT sentiment analysis for corporate action announcements.

Uses ProsusAI/finbert (already cached on Pi) to score announcement text
as positive/negative/neutral with confidence.

Output feeds into:
  1. Signal direction — positive CA → call bias, negative → put bias
  2. RL context — sentiment score stored with every trade for future learning
  3. Position sizing — high-confidence sentiment boosts Kelly scale slightly

Sentiment score: -1.0 (strongly bearish) to +1.0 (strongly bullish)
Confidence:       0.0 to 1.0
"""

import logging
import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Cache sentiment results to avoid re-running model on same text
CACHE_PATH = Path(str(Path(os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent.parent / 'data'))) / 'sentiment_cache.json'))

_model = None
_tokenizer = None
_cache: dict = {}   # in-memory cache so we don't hit disk on every call


def _load_model():
    """Lazy-load FinBERT — only pulled into memory when first needed."""
    global _model, _tokenizer
    if _model is not None:
        return
    try:
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        import torch
        logger.info('Loading FinBERT (ProsusAI/finbert) ...')
        _tokenizer = AutoTokenizer.from_pretrained('ProsusAI/finbert')
        _model     = AutoModelForSequenceClassification.from_pretrained('ProsusAI/finbert')
        _model.eval()
        logger.info('FinBERT loaded OK')
    except Exception as e:
        logger.error('Failed to load FinBERT: %s', e)


def _load_cache() -> Dict:
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cache(cache: Dict):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, 'w') as f:
        json.dump(cache, f, indent=2)


def _text_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def score_text(text: str) -> Dict:
    """
    Run FinBERT on a single text string.
    Returns: {score: float (-1 to 1), label: str, confidence: float, cached: bool}
    """
    if not text or not text.strip():
        return {'score': 0.0, 'label': 'neutral', 'confidence': 0.0, 'cached': False}

    # Check in-memory cache first (fast), then disk cache (persist across restarts)
    global _cache
    key = _text_hash(text)
    if key in _cache:
        result = dict(_cache[key])
        result['cached'] = True
        return result
    if not _cache:   # first call — load disk cache into memory
        _cache = _load_cache()
    if key in _cache:
        result = dict(_cache[key])
        result['cached'] = True
        return result

    _load_model()
    if _model is None:
        return {'score': 0.0, 'label': 'neutral', 'confidence': 0.0, 'cached': False}

    try:
        import torch
        # Truncate to 512 tokens
        inputs = _tokenizer(text, return_tensors='pt', truncation=True,
                            max_length=512, padding=True)
        with torch.no_grad():
            outputs = _model(**inputs)
            probs   = torch.softmax(outputs.logits, dim=-1)[0]

        # FinBERT label order: positive=0, negative=1, neutral=2
        labels = ['positive', 'negative', 'neutral']
        idx    = int(probs.argmax())
        label  = labels[idx]
        conf   = float(probs[idx])

        # Map to scalar: positive → +conf, negative → -conf, neutral → 0
        if label == 'positive':
            score = conf
        elif label == 'negative':
            score = -conf
        else:
            score = 0.0

        result = {'score': round(score, 4), 'label': label,
                  'confidence': round(conf, 4), 'cached': False}

        # Cache in memory + persist to disk
        _cache[key] = {k: v for k, v in result.items() if k != 'cached'}
        _save_cache(_cache)

        logger.info('FinBERT: "%s..." → %s (score=%.3f conf=%.3f)',
                    text[:80], label, score, conf)
        return result

    except Exception as e:
        logger.error('FinBERT inference failed: %s', e)
        return {'score': 0.0, 'label': 'neutral', 'confidence': 0.0, 'cached': False}


def score_corporate_action(action: Dict) -> Dict:
    """
    Score a single Alpaca corporate action announcement dict.
    Builds descriptive text from available fields and runs FinBERT.
    Returns sentiment dict + the original ca_type for context.
    """
    ca_type    = action.get('ca_type', '')
    initiating = action.get('initiating_symbol', '')
    target     = action.get('target_symbol', '')
    ratio      = action.get('ratio', '')
    date       = action.get('ex_date', action.get('record_date', ''))

    # Build a natural-language description FinBERT can reason about
    text_parts = []
    if ca_type == 'forward_split':
        text_parts.append(f'{initiating} announced a forward stock split')
        if ratio:
            text_parts.append(f'at a ratio of {ratio}')
        text_parts.append('indicating management confidence and broader retail access')
    elif ca_type == 'reverse_split':
        text_parts.append(f'{initiating} announced a reverse stock split')
        if ratio:
            text_parts.append(f'at a ratio of {ratio}')
        text_parts.append('often signaling financial distress or compliance requirements')
    elif ca_type in ('merger', 'cash_merger', 'stock_merger'):
        if target:
            text_parts.append(f'{initiating} announced a merger or acquisition of {target}')
        else:
            text_parts.append(f'{initiating} announced a merger transaction')
        text_parts.append('corporate merger announcement with strategic implications')
    elif ca_type == 'spinoff':
        text_parts.append(f'{initiating} announced a spinoff creating {target or "a new entity"}')
        text_parts.append('spinoffs often unlock hidden value for shareholders')
    elif ca_type == 'stock_dividend':
        text_parts.append(f'{initiating} declared a stock dividend')
        text_parts.append('positive signal of profitability and shareholder returns')
    elif ca_type == 'special_dividend':
        text_parts.append(f'{initiating} declared a special cash dividend')
        text_parts.append('one-time special dividend indicating strong cash position')
    else:
        text_parts.append(f'{initiating} corporate action: {ca_type}')

    if date:
        text_parts.append(f'effective date {date}')

    text = '. '.join(text_parts) + '.'
    sentiment = score_text(text)
    sentiment['ca_type']  = ca_type
    sentiment['symbol']   = initiating or target
    sentiment['raw_text'] = text
    return sentiment


def score_corporate_actions(actions: List[Dict]) -> List[Dict]:
    """Score a list of corporate action dicts. Returns list of sentiment results."""
    return [score_corporate_action(a) for a in actions]


def aggregate_sentiment(sentiments: List[Dict]) -> Dict:
    """
    Aggregate multiple sentiment scores into one summary.
    Weighted average by confidence.
    Returns: {score, label, confidence, n}
    """
    if not sentiments:
        return {'score': 0.0, 'label': 'neutral', 'confidence': 0.0, 'n': 0}

    total_weight = sum(s.get('confidence', 0.5) for s in sentiments)
    if total_weight == 0:
        return {'score': 0.0, 'label': 'neutral', 'confidence': 0.0, 'n': len(sentiments)}

    weighted_score = sum(
        s.get('score', 0.0) * s.get('confidence', 0.5)
        for s in sentiments
    ) / total_weight

    avg_conf = total_weight / len(sentiments)
    label = 'positive' if weighted_score > 0.1 else ('negative' if weighted_score < -0.1 else 'neutral')

    return {
        'score':      round(weighted_score, 4),
        'label':      label,
        'confidence': round(avg_conf, 4),
        'n':          len(sentiments),
    }


def get_symbol_sentiment(symbol: str, actions: List[Dict]) -> Dict:
    """
    Full pipeline: score all actions for a symbol, aggregate, return summary.
    Convenience wrapper for use in run_cycle().
    """
    if not actions:
        return {'score': 0.0, 'label': 'neutral', 'confidence': 0.0, 'n': 0}
    sentiments = score_corporate_actions(actions)
    result = aggregate_sentiment(sentiments)
    logger.info('FinBERT CA sentiment for %s: %s (score=%.3f, conf=%.3f, n=%d)',
                symbol, result['label'], result['score'],
                result['confidence'], result['n'])
    return result
