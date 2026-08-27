"""Vocabularies for the synthetic network generator.

This module lives on the *generator* side of the fence, not under backend/app,
and that placement is load-bearing. APPLICANT_MOTIVATION and APPLICANT_WORK are
keyed by intended strength band, so anything that can import this module can
recover the answer key for every applicant with a dictionary lookup. Phase 4
scores applicants from a rubric; if the scorer could import this, the score
would be circular and the evaluation meaningless.

Everything here is hand-written rather than pulled from a faker library. The
data has to look like *one private network* -- founders, operators, investors,
senior ICs and service providers who plausibly know each other -- and generic
name/company fakers produce noise without that texture.

The important structure is TOPICS. A topic is a single real-world thing
somebody in the network wants or can supply. Each topic carries separate
phrasings for the need side and the offer side, so when person A needs
"a founding account executive" and person B offers "recruiting GTM leaders for
early-stage companies", the two strings never literally match -- the
introduction engine has to earn the match semantically.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

FIRST_NAMES: list[str] = [
    "Aditya", "Aisha", "Akash", "Alejandro", "Alice", "Amara", "Ananya", "Andreas",
    "Aneesha", "Anjali", "Arjun", "Arun", "Aryan", "Beatriz", "Ben", "Bhavya",
    "Callum", "Camille", "Carlos", "Chen", "Chloe", "Daniel", "Deepak", "Devika",
    "Divya", "Eleanor", "Elena", "Emeka", "Emily", "Farhan", "Fatima", "Felix",
    "Gabriel", "Gaurav", "Grace", "Hannah", "Harsh", "Ibrahim", "Ines", "Isabel",
    "Ishaan", "Jacob", "James", "Jasmine", "Jennifer", "Jonas", "Joseph", "Kabir",
    "Karan", "Katherine", "Kavya", "Kenji", "Lakshmi", "Laura", "Leo", "Lucia",
    "Madhav", "Maya", "Meera", "Michael", "Miguel", "Mira", "Mohit", "Naomi",
    "Natasha", "Neha", "Nikhil", "Nina", "Nithya", "Olivia", "Omar", "Oscar",
    "Pallavi", "Patrick", "Pooja", "Prakash", "Pranav", "Priya", "Rachel", "Rahul",
    "Raj", "Ramesh", "Rebecca", "Renuka", "Ria", "Robert", "Rohan", "Rohit",
    "Ruchi", "Sahil", "Samir", "Sanjay", "Sara", "Shreya", "Siddharth", "Simran",
    "Sofia", "Sophie", "Sunil", "Tanvi", "Thomas", "Tobias", "Uma", "Varun",
    "Vikram", "Vinay", "Wei", "William", "Yash", "Yuki", "Zainab", "Zoe",
    # Accented spellings -- these exist so normalization has real work to do.
    "Chloé", "Inês", "José", "Léa", "Mónica", "Nuria", "Renée", "Sébastien",
]

LAST_NAMES: list[str] = [
    "Agarwal", "Ahmed", "Almeida", "Anand", "Bakshi", "Banerjee", "Bhat", "Bose",
    "Brennan", "Chandra", "Chatterjee", "Chen", "Chopra", "Clarke", "Cohen",
    "Costa", "Dasgupta", "Desai", "Dixit", "Doshi", "Dubois", "Fernandes",
    "Fischer", "Garcia", "Gill", "Goel", "Gopalan", "Gupta", "Hansen", "Hoffman",
    "Iyer", "Jain", "Joshi", "Kapoor", "Kaur", "Khanna", "Kholi", "Kim",
    "Krishnan", "Kulkarni", "Kumar", "Lal", "Larsen", "Lima", "Malhotra",
    "Mehta", "Menon", "Mishra", "Mitra", "Moreau", "Mukherjee", "Murthy",
    "Nair", "Nakamura", "Narayan", "Nguyen", "Okafor", "Oliveira", "Pandey",
    "Patel", "Pillai", "Prasad", "Raghavan", "Rao", "Reddy", "Rossi", "Roy",
    "Saxena", "Sharma", "Shetty", "Silva", "Singh", "Sinha", "Srinivasan",
    "Subramanian", "Tan", "Thomas", "Varghese", "Varma", "Verma", "Walsh",
    "Wang", "Weber", "Whitfield", "Yadav", "Zaidi",
]

# Nickname pairs drive one class of duplicate: the same human entered once
# formally and once casually.
NICKNAMES: dict[str, str] = {
    "michael": "Mike",
    "robert": "Rob",
    "william": "Will",
    "james": "Jim",
    "jennifer": "Jenny",
    "katherine": "Kate",
    "rebecca": "Becca",
    "thomas": "Tom",
    "daniel": "Dan",
    "joseph": "Joe",
    "sophie": "Soph",
    "siddharth": "Sid",
    "aditya": "Adi",
    "nikhil": "Nik",
    "priyanka": "Priya",
    "abhishek": "Abhi",
    "rajesh": "Raj",
    "vikram": "Vik",
    "samir": "Sam",
    "alexander": "Alex",
}

# (city, country, alternate spelling used as noise)
LOCATIONS: list[tuple[str, str, str | None]] = [
    ("Bengaluru", "India", "Bangalore"),
    ("Mumbai", "India", "Bombay"),
    ("Delhi NCR", "India", "Gurgaon"),
    ("Hyderabad", "India", None),
    ("Pune", "India", None),
    ("Chennai", "India", None),
    ("Singapore", "Singapore", None),
    ("Dubai", "United Arab Emirates", "DXB"),
    ("London", "United Kingdom", "London, UK"),
    ("Berlin", "Germany", None),
    ("Amsterdam", "Netherlands", None),
    ("Paris", "France", None),
    ("San Francisco", "United States", "SF Bay Area"),
    ("New York", "United States", "NYC"),
    ("Austin", "United States", None),
    ("Toronto", "Canada", None),
    ("Sydney", "Australia", None),
    ("Sao Paulo", "Brazil", None),
    ("Lagos", "Nigeria", None),
    ("Tokyo", "Japan", None),
]

# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------

SECTORS: list[tuple[str, str]] = [
    ("b2b_saas", "B2B SaaS"),
    ("fintech", "fintech"),
    ("healthtech", "healthtech"),
    ("climate", "climate tech"),
    ("devtools", "developer tools"),
    ("ecommerce", "e-commerce"),
    ("marketplace", "marketplaces"),
    ("ai_infra", "AI infrastructure"),
    ("cybersecurity", "cybersecurity"),
    ("logistics", "logistics"),
    ("edtech", "edtech"),
    ("consumer", "consumer apps"),
]

STAGES: list[tuple[str, str]] = [
    ("pre_seed", "pre-seed"),
    ("seed", "seed-stage"),
    ("series_a", "Series A"),
    ("series_b", "Series B"),
    ("growth", "growth-stage"),
    ("public", "public"),
]

COMPANY_STEMS: list[str] = [
    "Arc", "Atlas", "Aurora", "Axiom", "Beacon", "Bolt", "Cadence", "Canopy",
    "Cardinal", "Cedar", "Cipher", "Clarity", "Compass", "Coral", "Cortex",
    "Delta", "Ember", "Fathom", "Ferro", "Flint", "Forge", "Fulcrum", "Glide",
    "Granite", "Harbor", "Helix", "Indigo", "Juniper", "Kite", "Ledger",
    "Lumen", "Meridian", "Mosaic", "Nimbus", "Northwind", "Obsidian", "Orbit",
    "Parallel", "Pinnacle", "Quanta", "Quill", "Rally", "Relay", "Ridge",
    "Sable", "Signal", "Slate", "Solstice", "Spruce", "Stellar", "Summit",
    "Tessera", "Thicket", "Tide", "Torus", "Vantage", "Verdant", "Vertex",
    "Vessel", "Waypoint", "Willow", "Zenith",
]

COMPANY_TAILS: list[str] = [
    "AI", "Labs", "Systems", "Technologies", "Works", "Cloud", "Data", "Health",
    "Pay", "Logic", "Stack", "Grid", "Base", "Flow", "Loop", "Ops", "",
]

FUND_TAILS: list[str] = [
    "Capital", "Ventures", "Partners", "Fund", "Growth Partners", "Ventures Asia",
]

SERVICE_FIRM_TAILS: list[str] = [
    "Talent", "Search", "Advisory", "Studio", "Consulting", "Collective",
    "Legal", "& Co", "Compliance", "Recruiting",
]

# Suffix variants used to create company-name-variant duplicates.
COMPANY_SUFFIX_NOISE: list[str] = [
    " Inc.", " Inc", ", Inc.", " Ltd", " Ltd.", " Pvt Ltd", " Private Limited",
    " Technologies", " (formerly Northwind)", "",
]

SOURCES: list[str] = [
    "airtable_export",
    "event_signup",
    "referral",
    "linkedin_import",
    "newsletter_signup",
    "portfolio_intro",
]

APPLICANT_SOURCE = "applicant_form"

# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

TITLES: dict[str, dict[str, list[str]]] = {
    "founder": {
        "founder": [
            "Founder & CEO", "Co-founder & CEO", "Co-founder & CTO",
            "Founder", "Co-founder & COO", "Founder & CPO",
        ],
    },
    "operator": {
        "senior": [
            "VP Sales", "VP Marketing", "VP Engineering", "VP Operations",
            "Head of Growth", "Head of Product", "Head of People",
            "Chief of Staff", "Director of Revenue Operations",
            "Head of Customer Success", "GM, India", "COO",
        ],
        "mid": [
            "Sr. Manager, Growth", "Product Manager", "Sales Manager",
            "Marketing Manager", "Operations Manager", "Engineering Manager",
        ],
    },
    "investor": {
        "senior": [
            "Partner", "General Partner", "Managing Partner", "Principal",
            "Investment Director", "Angel Investor",
        ],
        "mid": ["Investment Associate", "Senior Associate"],
    },
    "ic": {
        "senior": [
            "Staff Software Engineer", "Principal Engineer", "Staff Data Scientist",
            "Senior Machine Learning Engineer", "Senior Product Designer",
            "Senior Data Engineer", "Security Engineer", "Solutions Architect",
        ],
        "mid": [
            "Software Engineer", "Data Analyst", "Product Designer",
            "Automation Engineer", "Backend Engineer",
        ],
    },
    "service_provider": {
        "senior": [
            "Founder & Principal Consultant", "Managing Director",
            "Partner, Executive Search", "Fractional CFO", "Fractional CMO",
            "Head of Talent Partnerships", "Corporate Counsel",
        ],
        "mid": ["Recruitment Consultant", "Brand Strategist", "Growth Consultant"],
    },
}

# Two spellings of the same job, used to create title-variant duplicates.
TITLE_VARIANTS: list[tuple[str, str]] = [
    ("VP ", "Vice President of "),
    ("VP ", "V.P. "),
    ("Sr. ", "Senior "),
    ("Head of Growth", "Growth Lead"),
    ("Head of Product", "Product Lead"),
    ("Co-founder & CEO", "Cofounder and CEO"),
    ("Founder & CEO", "CEO & Founder"),
    ("Chief of Staff", "Chief of Staff to the CEO"),
    ("General Partner", "GP"),
    ("Director of Revenue Operations", "Dir. of RevOps"),
    ("Staff Software Engineer", "Staff Engineer"),
    ("Head of Customer Success", "Customer Success Lead"),
]

# ---------------------------------------------------------------------------
# Needs / offers topic taxonomy
# ---------------------------------------------------------------------------

PERSONAS = ("founder", "operator", "investor", "ic", "service_provider")


@dataclass(frozen=True)
class Topic:
    id: str
    label: str
    needs: tuple[str, ...]
    offers: tuple[str, ...]
    need_personas: tuple[str, ...]
    offer_personas: tuple[str, ...]
    sectors: tuple[str, ...] = field(default=())  # empty tuple == any sector


TOPICS: list[Topic] = [
    Topic(
        id="gtm_hiring",
        label="senior GTM hiring",
        needs=(
            "hiring senior GTM talent for our {stage} team",
            "finding a founding account executive who has sold {sector} before",
            "building out a first sales team without a VP Sales in place",
        ),
        offers=(
            "recruiting senior GTM leaders for early-stage companies",
            "helping founders make their first sales hires",
            "building B2B sales teams from the first rep to the first VP",
        ),
        need_personas=("founder", "operator"),
        offer_personas=("operator", "service_provider"),
    ),
    Topic(
        id="eng_leadership_hiring",
        label="engineering leadership hiring",
        needs=(
            "hiring a first engineering manager for a team of nine",
            "finding a VP Engineering who has scaled past 50 engineers",
            "replacing a technical co-founder who has stepped back",
        ),
        offers=(
            "recruiting senior engineering leaders across India and SEA",
            "advising on engineering org design between 10 and 100 people",
            "running technical leadership searches for {sector} companies",
        ),
        need_personas=("founder", "operator"),
        offer_personas=("operator", "service_provider", "ic"),
    ),
    Topic(
        id="outbound_automation",
        label="outbound automation",
        needs=(
            "improving outbound automation so reps stop doing manual research",
            "fixing a cold email motion that has stopped converting",
            "instrumenting outbound sequencing and reply tracking properly",
        ),
        offers=(
            "building automated outbound systems that book meetings reliably",
            "designing cold outbound playbooks for {sector} teams",
            "wiring CRM, enrichment and sequencing tools into one pipeline",
        ),
        need_personas=("founder", "operator"),
        offer_personas=("operator", "service_provider"),
    ),
    Topic(
        id="fundraising_seed",
        label="seed fundraising",
        needs=(
            "warm introductions to seed funds that back {sector}",
            "getting a seed deck and narrative into fundable shape",
            "raising a seed round in the next two quarters",
        ),
        offers=(
            "seed fundraising experience and warm investor introductions",
            "helping founders sharpen the seed narrative before they pitch",
            "writing first cheques into {sector} at pre-seed and seed",
        ),
        need_personas=("founder",),
        offer_personas=("investor", "founder"),
    ),
    Topic(
        id="fundraising_growth",
        label="Series A and B fundraising",
        needs=(
            "preparing metrics and data room for a Series A process",
            "introductions to growth investors who understand {sector}",
            "benchmarking our numbers before we go out to raise",
        ),
        offers=(
            "leading Series A and Series B rounds in {sector}",
            "running institutional fundraising processes end to end",
            "diligence-ready metrics reviews before a growth round",
        ),
        need_personas=("founder", "operator"),
        offer_personas=("investor", "service_provider"),
    ),
    Topic(
        id="finance_leadership",
        label="finance leadership",
        needs=(
            "finding a B2B SaaS CFO who has been through an audit",
            "getting financial modelling and revenue reporting in order",
            "hiring a finance lead before our next board cycle",
        ),
        offers=(
            "fractional CFO work for {stage} companies",
            "building financial models and investor reporting from scratch",
            "placing CFOs and finance leaders into venture-backed companies",
        ),
        need_personas=("founder", "operator"),
        offer_personas=("service_provider", "operator"),
    ),
    Topic(
        id="pricing_packaging",
        label="pricing and packaging",
        needs=(
            "rethinking pricing and packaging as we move upmarket",
            "moving from seat-based pricing to usage-based pricing",
            "working out what to charge enterprise buyers",
        ),
        offers=(
            "pricing and packaging work for {sector} companies",
            "repricing products for enterprise buyers without churning SMB",
            "usage-based pricing design and migration",
        ),
        need_personas=("founder", "operator"),
        offer_personas=("operator", "service_provider"),
    ),
    Topic(
        id="security_compliance",
        label="security and compliance",
        needs=(
            "getting SOC 2 done before an enterprise deal closes",
            "answering enterprise security questionnaires without stalling deals",
            "standing up a security programme with no security hire",
        ),
        offers=(
            "taking companies through SOC 2 and ISO 27001",
            "security reviews and enterprise questionnaire support",
            "building application security practice inside product teams",
        ),
        need_personas=("founder", "operator"),
        offer_personas=("ic", "service_provider"),
    ),
    Topic(
        id="ai_evals",
        label="LLM evaluation and reliability",
        needs=(
            "setting up evaluations for an LLM feature we shipped too fast",
            "making an AI product behave predictably enough to sell",
            "reducing hallucinations in a customer-facing assistant",
        ),
        offers=(
            "building evaluation harnesses for LLM products",
            "making AI features reliable enough for enterprise buyers",
            "prompt and retrieval engineering with measurable quality gates",
        ),
        need_personas=("founder", "operator", "ic"),
        offer_personas=("ic", "operator", "service_provider"),
    ),
    Topic(
        id="internal_automation",
        label="internal AI automation",
        needs=(
            "automating internal ops work that eats the team's week",
            "replacing spreadsheet-and-Airtable workflows with something automated",
            "cutting manual data entry across our internal tools",
        ),
        offers=(
            "building internal automation with LLMs and workflow tooling",
            "replacing manual back-office processes with automated pipelines",
            "internal tooling that removes repetitive operations work",
        ),
        need_personas=("founder", "operator", "investor"),
        offer_personas=("ic", "operator", "service_provider"),
    ),
    Topic(
        id="data_infrastructure",
        label="data infrastructure",
        needs=(
            "getting analytics off production and into a real warehouse",
            "building a data team that can answer questions in hours not weeks",
            "fixing event tracking that nobody trusts",
        ),
        offers=(
            "designing data platforms and warehouse migrations",
            "setting up analytics stacks for {stage} companies",
            "building trustworthy product analytics and event schemas",
        ),
        need_personas=("founder", "operator"),
        offer_personas=("ic", "service_provider"),
    ),
    Topic(
        id="india_entry",
        label="India go-to-market",
        needs=(
            "entering the India market without a local team",
            "understanding India pricing and distribution realities",
            "hiring a first India-based operator",
        ),
        offers=(
            "building India go-to-market for international companies",
            "running India operations and local hiring",
            "distribution partnerships across India and the Gulf",
        ),
        need_personas=("founder", "operator", "investor"),
        offer_personas=("operator", "founder", "service_provider"),
    ),
    Topic(
        id="us_entry",
        label="US market entry",
        needs=(
            "selling into the US from a team based outside it",
            "setting up a US entity and first US hires",
            "positioning for US buyers who have not heard of us",
        ),
        offers=(
            "taking non-US companies into the US market",
            "US entity setup, hiring and first enterprise logos",
            "repositioning international products for US buyers",
        ),
        need_personas=("founder", "operator"),
        offer_personas=("operator", "founder", "service_provider"),
    ),
    Topic(
        id="performance_marketing",
        label="performance marketing",
        needs=(
            "getting paid acquisition to work at a sane CAC",
            "scaling spend without payback getting worse",
            "hiring or outsourcing performance marketing",
        ),
        offers=(
            "performance marketing for {sector} at scale",
            "paid acquisition audits and CAC payback modelling",
            "running growth experiments across paid and lifecycle",
        ),
        need_personas=("founder", "operator"),
        offer_personas=("operator", "service_provider"),
    ),
    Topic(
        id="content_seo",
        label="content and organic growth",
        needs=(
            "building an organic channel that is not dependent on ads",
            "content that ranks and actually converts",
            "getting founder-led distribution working consistently",
        ),
        offers=(
            "content strategy and SEO for {sector} companies",
            "founder-led content programmes that generate inbound",
            "editorial and technical content production",
        ),
        need_personas=("founder", "operator"),
        offer_personas=("operator", "service_provider"),
    ),
    Topic(
        id="retention",
        label="retention and customer success",
        needs=(
            "reducing churn in the first 90 days after onboarding",
            "building a customer success function from nothing",
            "understanding why accounts quietly stop using the product",
        ),
        offers=(
            "building customer success and retention programmes",
            "onboarding redesign that lifts activation and retention",
            "churn diagnostics for {sector} companies",
        ),
        need_personas=("founder", "operator"),
        offer_personas=("operator", "service_provider"),
    ),
    Topic(
        id="plg",
        label="product-led growth",
        needs=(
            "adding a self-serve motion alongside enterprise sales",
            "getting free-to-paid conversion above single digits",
            "designing a product-led funnel from scratch",
        ),
        offers=(
            "designing product-led growth motions and self-serve funnels",
            "free-to-paid conversion work for {sector} products",
            "activation and onboarding experimentation",
        ),
        need_personas=("founder", "operator"),
        offer_personas=("operator", "ic"),
    ),
    Topic(
        id="brand_design",
        label="brand and product design",
        needs=(
            "a rebrand before we go upmarket",
            "product design help for a first enterprise-grade release",
            "a design system so the product stops feeling inconsistent",
        ),
        offers=(
            "brand and product design for {stage} companies",
            "design systems and enterprise-grade UI work",
            "positioning and visual identity work for technical products",
        ),
        need_personas=("founder", "operator"),
        offer_personas=("ic", "service_provider"),
    ),
    Topic(
        id="legal_structuring",
        label="legal and structuring",
        needs=(
            "flipping the holding structure ahead of a raise",
            "cleaning up cap table and ESOP paperwork",
            "commercial contract templates for enterprise customers",
        ),
        offers=(
            "startup legal work: structuring, ESOP and commercial contracts",
            "cross-border holding structures for venture-backed companies",
            "cap table clean-ups before institutional rounds",
        ),
        need_personas=("founder",),
        offer_personas=("service_provider",),
    ),
    Topic(
        id="board_governance",
        label="board and governance",
        needs=(
            "running board meetings that are useful rather than performative",
            "finding an independent board member with {sector} depth",
            "first-time CEO coaching through a hard year",
        ),
        offers=(
            "board seats and governance experience in {sector}",
            "coaching first-time founders through scaling",
            "independent board roles for venture-backed companies",
        ),
        need_personas=("founder",),
        offer_personas=("investor", "founder"),
    ),
    Topic(
        id="dealflow",
        label="deal flow and diligence",
        needs=(
            "better deal flow in {sector} outside the usual networks",
            "technical diligence help on AI-heavy investments",
            "operators who will take advisory roles in portfolio companies",
        ),
        offers=(
            "technical diligence for investors looking at AI companies",
            "operator advisory work with portfolio companies",
            "sourcing and diligence support in {sector}",
        ),
        need_personas=("investor",),
        offer_personas=("ic", "operator", "founder"),
    ),
    Topic(
        id="client_pipeline",
        label="client pipeline for service firms",
        needs=(
            "a steadier pipeline of {stage} clients",
            "introductions to founders who need this kind of help",
            "positioning the practice so it stops competing on price",
        ),
        offers=(
            "introductions into a network of {stage} founders",
            "connecting good service partners with companies that need them",
            "vetted referrals for specialist advisory work",
        ),
        need_personas=("service_provider",),
        offer_personas=("founder", "operator", "investor"),
    ),
]

TOPICS_BY_ID: dict[str, Topic] = {t.id: t for t in TOPICS}

# ---------------------------------------------------------------------------
# Bios
# ---------------------------------------------------------------------------

BIO_TEMPLATES: dict[str, tuple[str, ...]] = {
    "founder": (
        "{first} is {article} {title} at {company}, {a_stage} {sector} company based in {city}. "
        "Started the company {years} years ago after {prior_line}. Currently focused on {focus}.",
        "{title} at {company} ({sector}, {stage}). Previously {prior_line}. "
        "Spends most of their time on {focus} out of {city}.",
    ),
    "operator": (
        "{title} at {company}, a {stage} {sector} company. {years} years in {sector}, "
        "previously {prior_line}. Based in {city}, working mainly on {focus}.",
        "{first} leads as {title} at {company} in {city}. Before that, {prior_line}. "
        "Known for {focus}.",
    ),
    "investor": (
        "{title} at {company}, investing in {sector} across {city} and the wider region. "
        "{years} years investing, previously {prior_line}. Writes about {focus}.",
        "{title} at {company}. Backs {stage} {sector} companies. Before investing, {prior_line}.",
    ),
    "ic": (
        "{title} at {company} in {city}. {years} years building {sector} systems, "
        "previously {prior_line}. Deep in {focus}.",
        "{first} is {article} {title} working on {focus} at {company}. Previously {prior_line}.",
    ),
    "service_provider": (
        "{title} at {company}, working with {stage} companies on {focus}. "
        "{years} years in the field, previously {prior_line}. Based in {city}.",
        "{title} at {company} ({city}). Works with {sector} founders on {focus}. Previously {prior_line}.",
    ),
}

PRIOR_LINES: tuple[str, ...] = (
    "eight years at a large enterprise software company",
    "an early role at a marketplace that reached unicorn status",
    "leading a regional team at a payments company",
    "a stint in management consulting",
    "building internal tooling at a logistics scale-up",
    "running growth at a consumer subscription app",
    "an engineering career in distributed systems",
    "two years at a seed fund",
    "founding a company that was acquired",
    "leading operations at a healthcare network",
    "a decade in executive search",
    "running a design studio for technical products",
)

# ---------------------------------------------------------------------------
# Membership applications
#
# Offline vets founders and senior operators for membership. An applicant is
# therefore not applying for a job -- they are asking to join a network, and the
# question is whether the network is better with them in it.
#
# The generator assigns each applicant an intended band (strong / review / weak)
# and that band is recorded ONLY in ground_truth.json. It must not be
# recoverable from the generated text, or the Phase 4 rubric becomes circular
# and the Phase 8 evaluation becomes a lookup table.
#
# So the templates below are SHARED ACROSS BANDS. Every band draws from the same
# eight phrasings of every field; what the band changes is the *values* plugged
# into them -- the stage, the traction numbers, the team size, how likely a
# referral is. A strong applicant and a review applicant can produce sentences
# with identical wording and different substance, which is exactly the
# discrimination a real rubric has to make.
# ---------------------------------------------------------------------------

# What they are building or running now. {traction} carries the real signal.
#
# Two banks, because a founder and a head of growth do not describe the same
# company the same way. Putting "Co-founded {company}" in a data analyst's
# application produced text that read as obviously synthetic.
APPLICATION_BUILDING_FOUNDER: tuple[str, ...] = (
    "Building {company}, {a_stage} {sector} company out of {city}. {traction}",
    "Running {company} - {sector}, {stage}, based in {city}. {traction}",
    "I run {company}, {a_stage} {sector} business in {city}. {traction}",
    "{company} is {a_stage} {sector} company I started in {city}. {traction}",
    "Currently building {company} ({sector}, {stage}) from {city}. {traction}",
    "Co-founded {company}, {a_stage} {sector} company headquartered in {city}. {traction}",
    "{company} - {sector}, {stage}. We operate out of {city}. {traction}",
    "Leading {company}, {a_stage} {sector} company in {city}. {traction}",
)

APPLICATION_BUILDING_OPERATOR: tuple[str, ...] = (
    "I am {role} at {company}, {a_stage} {sector} company in {city}. {traction}",
    "{role} at {company} - {sector}, {stage}, out of {city}. {traction}",
    "I run my part of {company} ({sector}, {stage}) from {city}, as {role}. {traction}",
    "Working as {role} at {company}, {a_stage} {sector} company based in {city}. {traction}",
    "{company} is {a_stage} {sector} company in {city}; I am {role} there. {traction}",
    "I look after {company}'s side of {sector} as {role}, based in {city}. {traction}",
    "{role} at {company} in {city} - {sector}, {stage}. {traction}",
    "I am part of the team at {company}, {a_stage} {sector} company in {city}, as {role}. {traction}",
)

# Traction phrasing by substance, not by band. Bands sample these with
# overlapping weights, so a strong applicant occasionally reads like a review
# one and the text alone never settles it.
TRACTION_BY_LEVEL: dict[str, tuple[str, ...]] = {
    "high": (
        "We are at about INR 6 crore ARR with 34 people and grew a little over 3x last year.",
        "Roughly INR 8 crore ARR, 40 in the team, net revenue retention sitting above 120%.",
        "Crossed INR 5 crore ARR this year across 60 enterprise accounts; team of 28.",
        "Around $1.2M ARR, profitable since last quarter, 31 people.",
        "We serve 90 paying businesses, roughly INR 7 crore ARR, and hire steadily.",
        "About INR 4.5 crore ARR, 25 people, and we have not raised since the Series A.",
        "We process INR 200 crore a year in volume for 140 customers; 38 in the team.",
        "Doubled to roughly $900K ARR with 22 people and a six-month payback.",
    ),
    "mid": (
        "We crossed INR 45 lakh ARR last quarter with 9 people.",
        "About INR 60 lakh ARR, 12 in the team, growing steadily if not spectacularly.",
        "Roughly 20 paying customers and INR 35 lakh ARR; there are 8 of us.",
        "We are at around $80K ARR with a team of 7 and our first enterprise pilot live.",
        "INR 50 lakh ARR, 11 people, and we are figuring out how to move upmarket.",
        "Two dozen customers, a little under INR 40 lakh ARR, 6 in the team.",
        "We have grown to INR 70 lakh ARR with 14 people over two years.",
        "About $50K ARR, 5 of us, and three pilots that should convert this quarter.",
    ),
    "low": (
        "We are pre-revenue with two of us, still testing the wedge.",
        "No revenue yet - three of us building toward a first pilot.",
        "Early: a prototype, a handful of design partners, and no paying customers.",
        "Just me and a contractor at the moment; we are validating the problem.",
        "We have a waitlist and no product in market yet.",
        "Pre-revenue, four people, and we are rewriting the plan for the second time.",
        "A few unpaid pilots running; nothing has converted so far.",
        "Two of us, six months in, still pre-revenue.",
    ),
}

# Why they want to join. {goal} carries the signal; the frame does not.
APPLICATION_WHY_JOIN: tuple[str, ...] = (
    "I want to be around people solving the same problems a year ahead of me. Mostly {goal}.",
    "The people I learn most from are other founders, and right now {goal}.",
    "I have been building fairly isolated. What I actually need is {goal}.",
    "Joining because {goal}, and that is hard to find outside a closed room.",
    "I would get the most out of this from {goal}.",
    "Honestly: {goal}. That is the whole reason.",
    "My peer group thinned out as we scaled. Looking for {goal}.",
    "What draws me is {goal} - the rest I can figure out alone.",
)

JOIN_GOALS: dict[str, tuple[str, ...]] = {
    "high": (
        "comparing notes with founders who have already been through a Series B",
        "finding two or three people I can call before making an irreversible decision",
        "meeting operators who have scaled past the wall I am about to hit",
        "candid conversations about the parts of the job nobody writes about",
        "peers at a similar stage who will tell me when I am wrong",
        "access to people who have hired the roles I am about to hire",
    ),
    "mid": (
        "learning from people a stage or two ahead of me",
        "meeting founders who have solved distribution in this market",
        "finding a few peers to compare notes with regularly",
        "getting introductions to the kind of talent we cannot reach yet",
        "honest feedback on whether the thing we are building has legs",
        "understanding what the next stage actually demands",
    ),
    "low": (
        "meeting people who have done this before",
        "finding mentors and maybe a co-founder",
        "learning how the ecosystem actually works",
        "getting in front of investors when the time comes",
        "being part of a community of ambitious people",
        "figuring out what I should be working on",
    ),
}

# What they would contribute. Rendered from the shared TOPICS taxonomy, so an
# applicant's offer is expressed in the same vocabulary as a member's -- which
# is what lets the introduction engine treat them identically later.
APPLICATION_CONTRIBUTION: tuple[str, ...] = (
    "Happy to help anyone with {offer}. I have done it more than once.",
    "I can be useful on {offer}.",
    "Where I would contribute: {offer}.",
    "I would offer {offer}, and introductions where they are useful.",
    "{offer} - that is the thing people usually come to me for.",
    "I am glad to spend time on {offer} for anyone earlier than me.",
    "Most useful to others on {offer}.",
    "I would bring {offer} and a fairly direct opinion.",
)

# Traction follows the company's stage, not the applicant's band. A Series B
# company has revenue; a pre-seed one usually does not. The overlap is real: a
# seed company occasionally has Series A numbers and vice versa, which is
# exactly why a scorer cannot read the stage word and stop thinking.
TRACTION_BY_STAGE: dict[str, tuple[str, ...]] = {
    "pre_seed": ("low", "low", "low", "low", "mid"),
    "seed":     ("mid", "mid", "mid", "low"),
    "series_a": ("high", "high", "mid", "mid"),
    "series_b": ("high", "high", "high", "mid"),
    "growth":   ("high", "high", "high", "high"),
    "public":   ("high", "high", "high", "mid"),
}

# What someone wants from a network tracks how far along they are.
GOAL_LEVEL_BY_STAGE: dict[str, tuple[str, ...]] = {
    "pre_seed": ("low", "low", "mid"),
    "seed":     ("mid", "mid", "low"),
    "series_a": ("mid", "high", "mid"),
    "series_b": ("high", "high", "mid"),
    "growth":   ("high", "high"),
    "public":   ("high", "high", "mid"),
}

# Who actually applies. Offline vets founders, so most applicants are founders --
# but senior operators apply too, and so do people who are not what the network
# is for. A pool of nothing but founders would make the weak band unreachable
# and the rubric untestable at its lower end.
APPLICANT_PERSONA_MIX = (("founder", 0.52), ("operator", 0.28), ("ic", 0.20))
APPLICANT_INDIA_SHARE = 0.8
