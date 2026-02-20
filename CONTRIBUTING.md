# Contributing to Send It Trading

Thanks for your interest in improving this system!

## How to Contribute

1. **Fork the repo**
2. **Create a feature branch:** `git checkout -b feature/amazing-feature`
3. **Make your changes**
4. **Test thoroughly**
5. **Commit:** `git commit -m 'Add amazing feature'`
6. **Push:** `git push origin feature/amazing-feature`
7. **Open a Pull Request**

## Areas for Contribution

### High Priority
- [ ] Live market data integration
- [ ] Web dashboard for monitoring
- [ ] Options strategy extensions
- [ ] Multi-asset support (crypto, futures)
- [ ] Improved IC calculation methods

### Medium Priority
- [ ] Macro regime detection
- [ ] Sector rotation signals
- [ ] Risk parity position sizing
- [ ] Tax-loss harvesting automation

### Nice to Have
- [ ] Jupyter notebook examples
- [ ] Video tutorials
- [ ] Case study documentation
- [ ] Performance benchmarking suite

## Code Style

- Use `black` for formatting: `black .`
- Use `flake8` for linting: `flake8 .`
- Write docstrings for public functions
- Add type hints where helpful

## Testing

All contributions should include tests:

```python
# test_your_feature.py
def test_new_feature():
    # Arrange
    system = MyFeature()
    
    # Act
    result = system.do_thing()
    
    # Assert
    assert result == expected_value
```

Run tests: `pytest -v`

## Pull Request Guidelines

**Good PR:**
- Clear description of what changed and why
- Tests included
- Documentation updated
- Follows existing code style

**Will be rejected:**
- Breaking changes without discussion
- No tests
- Uncommented complex logic
- Hardcoded secrets/credentials

## Questions?

Open an issue or ask in the PR discussion.

---

**Remember:** This is a tool for measuring edge and capturing asymmetric moves.  
Keep contributions aligned with that philosophy.
