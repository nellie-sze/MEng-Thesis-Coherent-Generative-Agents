NUM_DAYS = 2
START_DAY = "Monday"
REGENERATE_DESCRIPTIONS = False

# FOOTBALL MATCH SCENARIO

DAILY_CONTEXTS = [
    {
        'deactivate': ['air_taxi', '9_euro_PT_monthly_pass'],
    },
    {
        "environmental_context":"Due to the football match this afternoon, increased levels of congestion should be expected",
        'deactivate': ['air_taxi', '9_euro_PT_monthly_pass'],
        "events": [
            {
                "name": "football_match",
                "start_time": "15:30",
                "end_time": "18:30",
                "location_id": "120445501",
                "description": "Bundesliga football match between Bayern Munich and Borussia Dortmund at Stadion Rehberge",
            }
        ],
    },
]
