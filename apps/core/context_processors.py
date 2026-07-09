def fcu_branding(request):
    """Exposes brand constants to every template (nav, footer, page titles)."""
    return {
        "FCU_NAME": "Financial Cycle Unit",
        "FCU_SHORT_NAME": "FCU-MIS",
        "FCU_TAGLINE": "Integrity · Accountability · Transparency · Teamwork",
    }
