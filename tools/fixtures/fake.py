"""Fake identities for synthetic fixtures.

Every identifier comes from a range that cannot belong to a real person:
SSNs from the SSA advertising block 987-65-4320..4329, EINs with the
never-issued 00- prefix, phones from the fictional 555-0100..0199 block,
emails at example.com, and card numbers that are published test numbers.
"""

from dataclasses import dataclass, field
from random import Random

FAKE_SSNS = [f"987-65-{n}" for n in range(4320, 4330)]
TEST_CARDS = ["4111111111111111", "5555555555554444", "378282246310005"]

FIRST = ["Jordan", "Casey", "Avery", "Riley", "Morgan", "Quinn", "Rowan", "Emerson",
         "Harper", "Sawyer", "Marisol", "Teodoro", "Anneliese", "Desmond", "Priya", "Kenji"]
LAST = ["Halvorsen", "Okafor", "Lindqvist", "Castellano", "Abernathy", "Nakamura",
        "Whitfield", "Delacroix", "Brennan", "Varga", "Thorne", "Iyer"]
# People who appear only in free text (a partnership's managers, an executor): not in the
# household, never in the profile, and not drawn from the lists above.
OUTSIDERS = [("Imogen", "Satterfield"), ("Bartholomew", "Achebe"), ("Lucia", "Ferreira-Kahn"),
             ("Tobias", "Wren"), ("Margarethe", "Oyelaran"), ("Cyrus", "Pemberton"),
             ("Ines", "Valdivia"), ("Gideon", "Marchetti")]
STREET = ["Juniper", "Alder", "Larkspur", "Quarry", "Hollis", "Bramble", "Fenwick", "Tamarack"]
SUFFIX = ["St", "Ave", "Ln", "Ct", "Rd", "Way"]
CITIES = [("Madison", "WI", "537"), ("Boulder", "CO", "803"), ("Asheville", "NC", "288"),
          ("Tacoma", "WA", "984"), ("Burlington", "VT", "054"), ("Duluth", "MN", "558")]

# Organizations whose names are kept (public institutions).
PUBLIC_ORGS = {
    "bank": "Contoso Bank, N.A.",
    "broker": "Northwind Securities LLC",
    "custodian": "Contoso Trust Company",
    "daf": "Fabrikam Charitable Fund",
    "merchant": "Tailspin Hardware",
}
CHARITIES = ["Lakeside Food Bank", "River Valley Land Trust", "Northside Public Library Foundation",
             "Coastal Animal Rescue", "Midwest Literacy Project"]


@dataclass
class Address:
    street: str
    city: str
    state: str
    zip: str

    @property
    def city_line(self) -> str:
        return f"{self.city}, {self.state} {self.zip}"

    def lines(self) -> list[str]:
        return [self.street, self.city_line]


@dataclass
class Person:
    first: str
    middle: str
    last: str
    ssn: str
    dob: str  # MM/DD/YYYY
    role: str  # taxpayer | spouse | dependent | employee | beneficiary
    address: Address | None = None

    @property
    def name(self) -> str:
        return f"{self.first} {self.middle} {self.last}"

    @property
    def short_name(self) -> str:
        return f"{self.first} {self.last}"

    @property
    def third_party(self) -> bool:
        return self.role in ("employee", "beneficiary")


@dataclass
class Org:
    name: str
    ein: str
    address: Address
    public: bool  # public institutions keep their name; private ones are redacted
    phone: str = ""


@dataclass
class Household:
    taxpayer: Person
    spouse: Person
    dependent: Person
    employee: Person  # household employee (third party)
    beneficiaries: list[Person]  # IRA beneficiaries not in the household (third parties)
    address: Address
    phone: str
    email: str
    household_ein: str  # EIN the household uses as a household employer
    accounts: dict[str, str]  # label -> full account number
    routing: str
    card: str
    employer: Org
    partnership: Org
    s_corp: Org
    trust: Org
    payers: dict[str, Org] = field(default_factory=dict)

    def people(self) -> list[Person]:
        return [self.taxpayer, self.spouse, self.dependent, self.employee, *self.beneficiaries]

    def profile(self) -> dict:
        """What the user would enter once: their own household, not third parties."""
        own = [self.taxpayer, self.spouse, self.dependent]
        return {
            "people": [{"name": p.name, "ssn": p.ssn, "dob": p.dob, "role": p.role} for p in own],
            "addresses": [{"street": self.address.street, "city": self.address.city,
                           "state": self.address.state, "zip": self.address.zip}],
            "phones": [self.phone],
            "emails": [self.email],
            "eins": [self.household_ein],
            "accounts": sorted(self.accounts.values()),
            "cards": [self.card],
            "find_others": True,  # the corpus also tests the detectors that find third parties
        }


def ein(rng: Random) -> str:
    return f"00-{rng.randrange(10**6, 10**7)}"


def digits(rng: Random, n: int) -> str:
    return str(rng.randrange(1, 10)) + "".join(str(rng.randrange(10)) for _ in range(n - 1))


def routing(rng: Random) -> str:
    """ABA-checksum-valid routing number with the 00 (US Government) prefix."""
    d = [0, 0] + [rng.randrange(10) for _ in range(6)]
    weights = [3, 7, 1, 3, 7, 1, 3, 7]
    check = (10 - sum(a * b for a, b in zip(d, weights, strict=True)) % 10) % 10
    return "".join(map(str, d + [check]))


def cusip(rng: Random) -> str:
    alphabet = "0123456789ABCDEFGHJKLMNPRSTUVWXYZ"
    base = "".join(rng.choice(alphabet) for _ in range(8))
    total = 0
    for i, ch in enumerate(base):
        v = int(ch) if ch.isdigit() else ord(ch) - 55
        if i % 2:
            v *= 2
        total += v // 10 + v % 10
    return base + str((10 - total % 10) % 10)


def amount(rng: Random, lo: float = 10, hi: float = 50_000) -> str:
    return f"{rng.uniform(lo, hi):,.2f}"


def address(rng: Random) -> Address:
    city, state, zip3 = rng.choice(CITIES)
    street = f"{rng.randrange(100, 9999)} {rng.choice(STREET)} {rng.choice(SUFFIX)}"
    return Address(street, city, state, f"{zip3}{rng.randrange(10, 99)}")


def dob(rng: Random, year_lo: int, year_hi: int) -> str:
    return f"{rng.randrange(1, 13):02d}/{rng.randrange(1, 29):02d}/{rng.randrange(year_lo, year_hi)}"


def household(seed: int) -> Household:
    rng = Random(f"household:{seed}")
    first = rng.sample(FIRST, 7)
    last, emp_last, ben_last, *org_last = rng.sample(LAST, 7)
    ssns = iter(rng.sample(FAKE_SSNS, len(FAKE_SSNS)))
    home = address(rng)

    def person(first: str, last: str, role: str, years: tuple[int, int], addr: Address | None) -> Person:
        return Person(first, rng.choice("ABCDEFGHJKLMNPRSTW"), last, next(ssns), dob(rng, *years), role, addr)

    taxpayer = person(first[0], last, "taxpayer", (1960, 1990), home)
    spouse = person(first[1], last, "spouse", (1960, 1990), home)
    dependent = person(first[2], last, "dependent", (2008, 2020), home)
    employee = person(first[3], emp_last, "employee", (1965, 2000), address(rng))
    beneficiaries = [person(first[4], ben_last, "beneficiary", (1950, 1995), address(rng)),
                     person(first[5], last, "beneficiary", (1990, 2005), address(rng))]

    def org(name: str, public: bool) -> Org:
        return Org(name, ein(rng), address(rng), public, f"(555) 555-01{rng.randrange(0, 100):02d}")

    hh = Household(
        taxpayer=taxpayer, spouse=spouse, dependent=dependent, employee=employee,
        beneficiaries=beneficiaries, address=home,
        phone=f"(555) 555-01{rng.randrange(0, 100):02d}",
        email=f"{first[0].lower()}.{last.lower()}@example.com",
        household_ein=ein(rng),
        accounts={k: digits(rng, n) for k, n in
                  [("bank", 10), ("brokerage", 8), ("ira", 9), ("daf", 7), ("loyalty", 12)]},
        routing=routing(rng),
        card=rng.choice(TEST_CARDS),
        employer=org(f"{org_last[0]} & {org_last[1]} Engineering LLC", public=False),
        partnership=org(f"{rng.choice(STREET)} Ridge Partners LP", public=False),
        s_corp=org(f"{org_last[2]} Design Studio Inc", public=False),
        trust=org(f"The {last} Family Trust", public=False),
    )
    for key, name in PUBLIC_ORGS.items():
        hh.payers[key] = org(name, public=True)
    state = home.state
    hh.payers["state"] = org(f"State of {state} Department of Revenue", public=True)
    hh.payers["consultancy"] = org(f"{org_last[3]} Advisory Group", public=False)
    return hh
