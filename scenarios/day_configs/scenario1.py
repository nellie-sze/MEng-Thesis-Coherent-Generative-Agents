NUM_DAYS = 3
START_DAY = "Thursday"
REGENERATE_DESCRIPTIONS = False

# 9 EURO MONTHLY PASS SCENARIO

DAILY_CONTEXTS = [
    {
        "deactivate":['air_taxi', '9_euro_PT_monthly_pass'],
    },
    {
        "environmental_context": (f"The German government has introduced an experimental policy where the price of a nationwide public"
                                f"transport monthly pass has been reduced from 63 EUR to 9 EUR"),
        "deactivate":['air_taxi', 'PT_monthly_pass'],
    },
    {
        "environmental_context": (f"The German government has introduced an experimental policy where the price of a nationwide public"
                                f"transport monthly pass has been reduced from 63 EUR to 9 EUR"),
        "deactivate":['air_taxi', 'PT_monthly_pass'],
    },
]
