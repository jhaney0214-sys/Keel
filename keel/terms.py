"""The words a report uses, by institution.

Keel is written in a credit union's words: shares, net worth, NEV. A bank
reads the same measures as deposits, equity and EVE. Everything a page says
is written once, and a bank's page is translated as its last step, so the
two can never say different things about the same number. The regulator's
own tests are different: the NCUA NEV Supervisory Test and the 12 CFR 741.12
liquidity tiers apply only to credit unions, and a bank's report leaves them
out rather than translating them.
"""

import re

BANK = (
    (r"\bNet economic value\b", "Economic value of equity"),
    (r"\bnet economic value\b", "economic value of equity"),
    (r"\bNEV\b", "EVE"),
    (r"\bNet worth\b", "Equity"),
    (r"\bnet worth\b", "equity"),
    (r"\bNon-maturity shares\b", "Non-maturity deposits"),
    (r"\bShares\b", "Deposits"),
    (r"\bshares\b", "deposits"),
    (r"\bshare (runoff|decay|rates|growth|money)\b", r"deposit \1"),
    (r"\bShare (runoff|decay|rates|growth)\b", r"Deposit \1"),
    (r"\bcredit union's\b", "bank's"),
    (r"\bcredit unions\b", "banks"),
    (r"\bCredit union\b", "Bank"),
    (r"\bcredit union\b", "bank"),
    (r"\bmembers\b", "customers"),
    (r"\bMembers\b", "Customers"),
    (r"\bmember\b", "customer"),
    (r"\bMember\b", "Customer"),
    (r"\bcertificates\b", "CDs"),
    (r"\bCertificates\b", "CDs"),
    (r"\bcertificate\b", "CD"),
    (r"\bCertificate\b", "CD"),
)
_BANK = [(re.compile(p), r) for p, r in BANK]


def is_bank(a):
    return getattr(a, "institution", "credit_union") == "bank"


def translate(text, a):
    """`text` in the institution's words. Leaves <code> and product keys alone
    because they never contain these words with word boundaries intact."""
    if not is_bank(a):
        return text
    parts = re.split(r"(<code>.*?</code>|<pre>.*?</pre>)", text, flags=re.S)
    for i in range(0, len(parts), 2):
        for pattern, replacement in _BANK:
            parts[i] = pattern.sub(replacement, parts[i])
    return "".join(parts)
