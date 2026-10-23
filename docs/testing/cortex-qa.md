# Cortex configuration import and CAN QA

The .NET import gate validates the complete document before changing models.
Unsupported format versions, invalid input positions, unknown enum values and
reversed PWM bounds are rejected. Partial current/PWM bounds are checked against
the retained values before any channel is changed. Format 1 and omitted version
remain supported. Input lists are positional: explicit InputNumber must agree
with the array position; omitted numbers preserve the existing number.

Run from the repository root:

```text
dotnet build tests/Cortex.Tests/Cortex.Tests.csproj -c Release -warnaserror
dotnet test tests/Cortex.Tests/Cortex.Tests.csproj -c Release --collect:"XPlat Code Coverage"
python -m pip install -r r2_pdm_configurator/tests/requirements-ci.txt
python -m coverage run --branch --source=r2_pdm_configurator -m unittest discover -s r2_pdm_configurator/tests -v
python -m coverage report --omit="*/tests/*"
```

GUI tests select Qt's offscreen platform before creating the application. They
intercept adapter creation and exercise connect, offline polling, bus failure
and cleanup. The virtual CAN fixture uses real python-can virtual endpoints and
services all 14 outputs, 8 analogue inputs and the digital mask; it asserts that
polling sends only diagnostic request identifiers. It never opens hardware.

CI runs Python 3.10/3.12 and .NET on Windows/Linux, with coverage uploaded as
artifacts even after a test failure. No source-branch report commits are made.
These fixtures validate software behavior only: legacy multiframe responses
lack a channel/transaction identifier and cannot prove device identity or
confirmed configuration writes. The STM32Cube firmware remains silent CAN and
non-actuating; this fake device models the legacy diagnostic protocol only.
