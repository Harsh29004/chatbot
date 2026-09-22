"""
Seven widget designs a customer can install on their own site.

A theme is pure presentation: colours, shape, layout and default copy. It
carries no rules about what the bot says — that stays with the bot's template
and sheet — so every theme works with every bot.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Layout = Literal["bubble", "drawer"]
HeaderStyle = Literal["solid", "gradient", "glass"]


@dataclass(frozen=True)
class WidgetTheme:
    id: str
    name: str
    tagline: str
    description: str
    best_for: tuple[str, ...]

    # Where the chat opens: a floating corner panel, or a full-height side drawer.
    layout: Layout
    header_style: HeaderStyle
    launcher_icon: Literal["chat", "spark", "help", "leaf", "wave"]

    # Palette
    primary: str
    primary_2: str
    on_primary: str
    background: str
    surface: str
    text: str
    muted: str
    border: str
    user_bubble: str
    user_text: str
    bot_bubble: str
    bot_text: str

    font_family: str
    radius: int
    dark: bool

    # Default copy — every one of these can be overridden at install time.
    title: str
    greeting: str
    placeholder: str


WIDGET_THEMES: tuple[WidgetTheme, ...] = (
    WidgetTheme(
        id="aurora",
        name="Aurora",
        tagline="Modern gradient, floating bubble",
        description="An indigo-to-violet gradient header with soft rounded bubbles. A safe, polished default for most sites.",
        best_for=("SaaS", "E-commerce", "Startups"),
        layout="bubble",
        header_style="gradient",
        launcher_icon="chat",
        primary="#5b5bf6",
        primary_2="#a855f7",
        on_primary="#ffffff",
        background="#ffffff",
        surface="#f5f6fb",
        text="#1d1f2c",
        muted="#6b7085",
        border="#e4e6f0",
        user_bubble="#5b5bf6",
        user_text="#ffffff",
        bot_bubble="#f0f1f8",
        bot_text="#1d1f2c",
        font_family='Inter, -apple-system, "Segoe UI", system-ui, sans-serif',
        radius=18,
        dark=False,
        title="Ask us anything",
        greeting="Hi there 👋 How can we help you today?",
        placeholder="Type your question…",
    ),
    WidgetTheme(
        id="midnight",
        name="Midnight",
        tagline="Dark mode with a neon edge",
        description="A deep navy panel with cyan accents. Made for dark websites, developer tools and gaming brands.",
        best_for=("Developer tools", "Gaming", "Tech"),
        layout="bubble",
        header_style="solid",
        launcher_icon="spark",
        primary="#22d3ee",
        primary_2="#0ea5e9",
        on_primary="#04121a",
        background="#0b0f17",
        surface="#121826",
        text="#e6edf7",
        muted="#8591a7",
        border="#1f2940",
        user_bubble="#22d3ee",
        user_text="#04121a",
        bot_bubble="#172033",
        bot_text="#e6edf7",
        font_family='"JetBrains Mono", Inter, ui-monospace, system-ui, sans-serif',
        radius=14,
        dark=True,
        title="Support",
        greeting="Hey! Ask me anything about the product.",
        placeholder="Ask a question…",
    ),
    WidgetTheme(
        id="paper",
        name="Paper",
        tagline="Minimal, monochrome, sharp",
        description="Black on white with square corners and no decoration. Disappears into editorial and luxury designs.",
        best_for=("Fashion", "Portfolios", "Publishing"),
        layout="bubble",
        header_style="solid",
        launcher_icon="help",
        primary="#111111",
        primary_2="#111111",
        on_primary="#ffffff",
        background="#ffffff",
        surface="#fafafa",
        text="#111111",
        muted="#737373",
        border="#e5e5e5",
        user_bubble="#111111",
        user_text="#ffffff",
        bot_bubble="#f2f2f2",
        bot_text="#111111",
        font_family='"Helvetica Neue", Helvetica, Arial, sans-serif',
        radius=4,
        dark=False,
        title="Questions?",
        greeting="Hello. What would you like to know?",
        placeholder="Write a question",
    ),
    WidgetTheme(
        id="sunset",
        name="Sunset",
        tagline="Warm, friendly, extra round",
        description="A coral-to-amber gradient with pill-shaped bubbles. Feels welcoming — built for food, retail and hospitality.",
        best_for=("Restaurants", "Retail", "Hospitality"),
        layout="bubble",
        header_style="gradient",
        launcher_icon="wave",
        primary="#f9573b",
        primary_2="#fb9d2c",
        on_primary="#ffffff",
        background="#fffaf6",
        surface="#fff1e8",
        text="#3a2418",
        muted="#8c6a58",
        border="#f6dccb",
        user_bubble="#f9573b",
        user_text="#ffffff",
        bot_bubble="#ffffff",
        bot_text="#3a2418",
        font_family='"Nunito", "Segoe UI", system-ui, sans-serif',
        radius=24,
        dark=False,
        title="We're here to help",
        greeting="Hello! 😊 Ask about our menu, hours, orders or anything else.",
        placeholder="Ask us something…",
    ),
    WidgetTheme(
        id="sage",
        name="Sage",
        tagline="Calm green, gentle and trustworthy",
        description="Soft sage greens and generous spacing. Reassuring for clinics, wellness, education and non-profits.",
        best_for=("Clinics", "Wellness", "Education"),
        layout="bubble",
        header_style="solid",
        launcher_icon="leaf",
        primary="#2f7d62",
        primary_2="#4fa37f",
        on_primary="#ffffff",
        background="#fbfdfb",
        surface="#eef5f0",
        text="#1f3029",
        muted="#61786d",
        border="#dbe8df",
        user_bubble="#2f7d62",
        user_text="#ffffff",
        bot_bubble="#eef5f0",
        bot_text="#1f3029",
        font_family='"Source Sans 3", "Segoe UI", system-ui, sans-serif',
        radius=16,
        dark=False,
        title="How can we help?",
        greeting="Welcome. Ask about appointments, services, timings or fees.",
        placeholder="Type your question here",
    ),
    WidgetTheme(
        id="harbor",
        name="Harbor",
        tagline="Corporate navy, full-height drawer",
        description="A professional navy side drawer that slides in from the right edge. Room for long answers — suits finance, SaaS and B2B.",
        best_for=("Fintech", "B2B", "Real estate"),
        layout="drawer",
        header_style="solid",
        launcher_icon="chat",
        primary="#1e3a8a",
        primary_2="#2563eb",
        on_primary="#ffffff",
        background="#ffffff",
        surface="#f3f6fb",
        text="#0f1a33",
        muted="#5b6782",
        border="#dfe5f0",
        user_bubble="#1e3a8a",
        user_text="#ffffff",
        bot_bubble="#f3f6fb",
        bot_text="#0f1a33",
        font_family='"IBM Plex Sans", "Segoe UI", system-ui, sans-serif',
        radius=8,
        dark=False,
        title="Help centre",
        greeting="Good day. How can we assist you?",
        placeholder="Search or ask a question",
    ),
    WidgetTheme(
        id="glass",
        name="Glass",
        tagline="Frosted glass over your page",
        description="A translucent, blurred panel that lets your site show through. Striking over photography and bold hero sections.",
        best_for=("Travel", "Agencies", "Luxury"),
        layout="bubble",
        header_style="glass",
        launcher_icon="spark",
        primary="#7c3aed",
        primary_2="#ec4899",
        on_primary="#ffffff",
        background="rgba(255, 255, 255, 0.72)",
        surface="rgba(255, 255, 255, 0.55)",
        text="#1b1530",
        muted="#5f587a",
        border="rgba(255, 255, 255, 0.6)",
        user_bubble="#7c3aed",
        user_text="#ffffff",
        bot_bubble="rgba(255, 255, 255, 0.85)",
        bot_text="#1b1530",
        font_family='"Plus Jakarta Sans", Inter, system-ui, sans-serif',
        radius=20,
        dark=False,
        title="Chat with us",
        greeting="Hi ✨ Ask me anything — I answer instantly.",
        placeholder="Ask anything…",
    ),
)

DEFAULT_WIDGET_THEME_ID = "aurora"

_BY_ID = {theme.id: theme for theme in WIDGET_THEMES}


def get_widget_theme(theme_id: str) -> WidgetTheme | None:
    return _BY_ID.get(theme_id)


def widget_theme_dict(theme: WidgetTheme) -> dict[str, Any]:
    data = asdict(theme)
    data["best_for"] = list(theme.best_for)
    return data
