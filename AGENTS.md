# Generate code

Always comply with PEP 8 and PEP 257.

Never do any write operation in git, you can do status, diff, etc, anything that implies looking at data, but never commit, stage, branch, etc.

Never install anything in a virtualenv, or activate/deactivate one.

All comments, docstrings, variable names, etc, should be in English.


# Generate tests

Should be simpler and crispy, even if they have some repetition.

Always using `pytest` semantics. To mock, try to use the `mocker` fixture. To check logs, use the `logs` fixture.

Build a test file per code file we have. Inside each test file use a class to group tests by things that are tested, but do not use class inheritance. Rely more on fixtures than function helpers, but this is not a hard rule.


# Running the tests

All tests go in `tests/` directory.

To run them:

```
pytest tests/
```

You should be inside a virtualenv; if not, complain to the human.
