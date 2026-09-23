# dalicc

The Python client for [DALICC](https://dalicc.net), the Data Licenses Clearance Center,
and the `dalicc` command line tool. For anyone calling the DALICC API from Python or from
a shell.

DALICC models licenses as machine-readable policies and reasons over them: which licenses
can be combined, whether one contradicts itself, and what a composed license would have to
say.

## Install

```bash
pip install dalicc                                    # once it is on PyPI
pip install git+https://github.com/dalicc/python-sdk  # until then
```

Python 3.10 or newer. The only runtime dependency is `httpx`. The package uses a `src`
layout: the importable package is `dalicc`, in `src/dalicc/`.

## Quick start

```python
from dalicc import Client

with Client() as api:
    for license in api.licenses.list(keyword="apache"):
        print(license.id, license.title)

    verdict = api.reasoning.compatibility(["MIT", "GPL-3.0-only"])
    print("conflicts" if verdict.has_conflicts else "no conflicts")
```

One client object with seven groups of methods, named after the API rather than after the
client: `api.licenses`, `api.dependency_graphs`, `api.reasoning`, `api.composing`,
`api.translate`, `api.account` and `api.github`. `AsyncClient` offers the same methods with
`await`.

`base_url` defaults to `DALICC_BASE_URL` and then to `https://api.dalicc.net`. Searching,
reading and reasoning need no credentials. Composing, your own drafts and the translation
assistant need a personal API token from `/account/tokens`, passed as `Client(api_key=...)`
or in `DALICC_API_KEY`.

`api.translate` goes both ways: it reads a licence text and proposes a DALICC model for
it, with the quote behind every statement, and it writes the licence text of a model.
Reading a text needs `consent=True` on every call, because the text is sent to an
external model provider; do not submit confidential texts. Writing sends the model
instead, and `without_names=True` takes the creator, licensor and publisher names out of
it first.

```python
result = api.translate.text(open("terms.txt").read(), consent=True)
narration = api.translate.narrate("Apache-2.0")
```

A long licence is read in parts and takes minutes; the client waits it out for you.
The polling, the quotas and the reading that needs no provider at all are in the
reference.

## Command line

The package installs one console script, `dalicc`, with seven commands:

```bash
dalicc search apache                 # find licenses by a word in the title
dalicc get MIT --format ttl          # print one license document
dalicc check MIT GPL-3.0-only        # check two or more licenses for conflicts
dalicc consistency CC-BY-4.0         # check one license for contradictions within itself
dalicc compose my-license.json       # compose a license (needs a token)
dalicc translate terms.txt --consent # propose a model for a licence text (needs a token)
dalicc graphs                        # list the dependency graphs you may see
```

Four options come before the command: `--base-url`, `--api-key`, `--timeout` and
`--json`, which prints the service's own answer so the tool can go in a pipe. The exit
code tells a script what happened: `0` success, `1` the service or the network failed,
`2` the command line could not be read, `3` authentication, `4` nothing with that
identifier, `5` the request was refused, `6` a rate limit or a quota.

## Documentation

The endpoints this client calls are documented, with every parameter and every status
code, at [api.dalicc.net/docs](https://api.dalicc.net/docs), which is the live API and
answers today.

The full client reference, with an example for every method, the error and rate-limit
model and the command line reference, belongs on the DALICC website at
[dalicc.net/documentation](https://dalicc.net/documentation). That site is being moved to
the current generation of DALICC; until it is done, `https://dalicc.net` redirects to the
API documentation above, and this page and the docstrings in the package are the
reference.

## Development

The client is developed inside the DALICC service repository and mirrored here, so a
change is made there and copied over; this repository is the public home of the package
and the address its metadata points at.

From a checkout of this repository:

```bash
pip install -e ".[dev]"   # the client and its development dependencies
ruff check .              # the settings in pyproject.toml
python -m build           # a wheel and an sdist in dist/
```

`tests/` runs the client against the DALICC application in the same process rather than
over a network, which needs that application; the suite is therefore run in the service
repository, where it is part of every build.

## Licence

Apache-2.0. See [LICENSE](https://github.com/dalicc/python-sdk/blob/main/LICENSE) and
[NOTICE](https://github.com/dalicc/python-sdk/blob/main/NOTICE).

The client is permissively licensed although the DALICC service is AGPL-3.0-only: a
client that only talks to a public API has no reason to carry the service's terms with
it, and this one carries no license data either. Installing the package and calling the
API with it puts you under nothing but Apache-2.0.

Copyright 2021-2026 DALICC, Verein zur Foerderung der Rechtssicherheit in der
Datenbewirtschaftung (ZVR 1249185710). Questions:
tassilo.pellegrini@ustp.at or giray.havur@ustp.at.

Nothing this client returns is legal advice.
