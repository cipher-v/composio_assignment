"""Structured output schema shared by the research agent and the verifier.

The enums are deliberately small so the 100 rows can be clustered without
post-hoc normalisation. Definitions live in RUBRIC (fed to both agents) so
"self-serve" or "needs outreach" means the same thing on every row.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

AuthMethod = Literal[
    "oauth2", "api_key", "bearer_token", "basic", "jwt", "signature_hmac", "other", "none"
]
Access = Literal[
    "self_serve_free",        # sign up and get creds free (free tier / dev account / OSS)
    "self_serve_trial",       # creds only during a time-limited free trial
    "paid_plan",              # API only on a paid (non-enterprise) plan
    "approval_required",      # dev account is free but app review / admin approval / developer token approval needed
    "partner_or_sales",       # partnership program, enterprise contract or contact-sales gate
    "no_public_api",          # no documented public API at all
]
ApiStyle = Literal["rest", "graphql", "soap", "grpc", "websocket", "cli_or_library", "none"]
Breadth = Literal["broad", "moderate", "narrow", "none"]
McpStatus = Literal["official", "community_only", "none_found"]
Verdict = Literal["ready_now", "ready_with_friction", "needs_outreach", "not_feasible"]
Blocker = Literal[
    "none",
    "paid_plan",
    "app_review_or_approval",
    "partnership_or_sales",
    "enterprise_only",
    "no_public_api",
    "local_cli_only",
    "tos_or_rate_limits",
    "unclear_or_sparse_docs",
]
Claim = Literal["auth", "access", "api", "mcp", "verdict", "general"]


class Evidence(BaseModel):
    claim: Claim = Field(description="Which field this source supports")
    url: str = Field(description="Exact URL of the page (official docs preferred)")
    quote: str = Field(description="Short verbatim or near-verbatim snippet from the page supporting the claim")


class AppResearch(BaseModel):
    subcategory: str = Field(description="Specific category, e.g. 'Helpdesk / ticketing'")
    one_liner: str = Field(description="What the product does, one sentence, max ~20 words")
    auth_methods: list[AuthMethod] = Field(description="All auth methods the public API supports")
    primary_auth: AuthMethod = Field(description="The auth method a third-party integration (like Composio) would use")
    auth_notes: str = Field(description="Concrete detail, e.g. 'OAuth2 auth-code for public apps; private app tokens for single store'")
    access: Access
    access_notes: str = Field(description="What a developer must do to get working credentials, incl. plan names / approval steps")
    api_styles: list[ApiStyle]
    api_breadth: Breadth
    api_notes: str = Field(description="Surface description: main resources, rough endpoint count, versioning")
    openapi_spec: bool = Field(description="Public OpenAPI/Swagger/GraphQL schema published")
    webhooks: bool = Field(description="Documented webhooks / event subscriptions")
    sandbox: bool = Field(description="Free sandbox / test / developer environment available")
    mcp: McpStatus
    mcp_notes: str = Field(description="Name/URL of MCP server if any (official = maintained by the vendor)")
    verdict: Verdict
    blocker: Blocker = Field(description="Main blocker to building an agent toolkit today; 'none' if ready_now")
    blocker_notes: str
    evidence: list[Evidence] = Field(description="At least one source each for auth, access, api; plus mcp if claimed")
    confidence: float = Field(description="0-1 self-assessed confidence in the whole record")


class DocLinks(BaseModel):
    official_domain: str = Field(description="Vendor's main developer docs domain")
    auth_url: Optional[str] = Field(description="Authentication / API keys / OAuth docs page")
    getting_started_url: Optional[str]
    pricing_or_access_url: Optional[str] = Field(description="Pricing page or page stating which plans include API access / partner program")
    api_reference_url: Optional[str]
    mcp_url: Optional[str] = Field(description="MCP server docs or repo, if one exists")
    other_urls: list[str] = Field(description="Up to 3 more highly relevant official pages")


class Change(BaseModel):
    field: str
    old: str
    new: str
    reason: str
    url: str


class VerifiedApp(AppResearch):
    changes: list[Change] = Field(description="Every field you changed vs the first pass, with the source that proves it. Empty if nothing changed.")
    needs_human: bool = Field(description="True if sources conflict, docs are gated/unreadable, or you are not confident")
    needs_human_reason: str


RUBRIC = """
DEFINITIONS (apply strictly and consistently):
access:
  self_serve_free   - any developer can sign up and obtain working API credentials at no cost (free tier, free developer account, free sandbox, or open-source self-host).
  self_serve_trial  - credentials only obtainable during a time-limited free trial; afterwards a paid plan is needed.
  paid_plan         - API access requires a paid subscription (not enterprise-only) with no free tier/trial that includes API.
  approval_required - account is free, but the vendor must approve you before credentials work on other customers' accounts
                      (e.g. Google Ads developer token, Meta app review for advanced permissions, Amazon SP-API developer registration,
                      Google restricted-scope verification). An OPTIONAL review to be listed in a marketplace/app directory does NOT count:
                      if an unlisted OAuth app can already connect other users' accounts, it is still self-serve.
  partner_or_sales  - must join a partner program, sign an enterprise contract, or talk to sales to get API access.
  no_public_api     - there is no documented public API.
verdict (could Composio ship an agent toolkit today?):
  ready_now           - public docs + self-serve creds; a developer could build it this week.
  ready_with_friction - buildable but with a hurdle: paid plan, trial expiry, app review, per-customer admin setup, narrow API,
                        or it is an open-source CLI/library (no hosted API) that would have to be wrapped and run in a sandbox (blocker local_cli_only).
  needs_outreach      - blocked on partnership / sales / enterprise contract with the vendor.
  not_feasible        - no public API and nothing callable to wrap (e.g. consumer-only product with no API or CLI).
mcp:
  official       - the vendor itself publishes/hosts an MCP server.
  community_only - only third-party MCP servers exist.
  none_found     - no MCP server found.
primary_auth: what a multi-tenant integration platform would use (prefer oauth2 if the vendor offers public OAuth apps).
Use 'bearer_token' for static personal/access tokens that are not called 'API key'; 'api_key' for keys; 'basic' for HTTP Basic (incl. key-as-username).
Evidence must be real URLs you opened or saw in search results; prefer the vendor's official docs. Never invent URLs.
If you cannot determine something, say so in the notes and lower confidence rather than guessing.
"""
