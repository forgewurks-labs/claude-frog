# Contributing

Thanks for helping the frog. A few things keep this repo easy to work on:

## Ground rules

- **One stdlib-only file.** All runtime code lives in `claude_frog.py` and uses only the
  Python standard library — no third-party dependencies, in code or tests. If a change
  seems to need a dependency, open an issue first.
- **The frog never breaks your prompt.** The `tap` (statusline) and `hook` paths must
  never crash and must always exit 0, no matter what they're fed.
- **One frog per tmux window.** Pane spawning is lock-protected and reference-counted
  across sessions sharing a window; changes touching pane lifecycle need tests against
  the fake-tmux harness in `tests/test_frog.py`.

## Developing

There is nothing to install — clone and run:

```sh
python3 claude_frog.py preview          # see him without wiring anything
python3 -m unittest discover -s tests   # the full test suite
```

CI (GitHub Actions) runs a byte-compile gate plus the test suite on Python 3.9, 3.11,
and 3.13. Please make sure the suite passes on the Python you have locally before
opening a PR, and add tests for behavior you change.

## License

MIT (see [LICENSE](LICENSE)). By submitting a contribution you agree it is licensed
under the same terms.
