def build_schedule_prompt(building_options, description, home_type, daily_context=None, previous_day_schedule=None):
    daily_context_text = ""
    day = daily_context.day if daily_context is not None and getattr(daily_context, "day", None) else "today"
    if daily_context is not None:
        daily_context_text = f"Today is {daily_context.day}. "
        if daily_context.environmental_context:
            daily_context_text += (
                f"{daily_context.environmental_context}. "
                f"If relevant to your agent, consider this when planning your daily schedule.\n"
            )
        if daily_context.events:
            for event in daily_context.events:
                daily_context_text += (
                    f"Today, there will be {event.description} from {event.start_time} until {event.end_time}.\n"
                )
            daily_context_text += (
                f"It is not compulsory to attend. You can attend these events only between the given times. "
                f"If you would like to travel and attend an event in person, add it to your schedule in the given time slot and set the building type as {event.name}. "
                f"You must still end the day by returning home when attending events."
            )
    previous_day_text = ""
    if previous_day_schedule is not None:
        previous_day_text = (
            f"Here is a summary of your previous day's schedule for continuity:\n"
            f"{previous_day_schedule.to_compact_summary()}\n"
            f"Use it as context for planning today naturally, but do not copy it mechanically.\n"
        )

    return (
        f'You are:\n{description}\n\n'
        f'Write in broad strokes what you are doing during the day. Start and finish the day at home. ' 
        f'Only include tasks that occur at a specific location which must be one of the provided building options.'
        f'For the first and final tasks, which will be at home, the building type must be {home_type}\n'
        f'Only start a new task if it involves a location change (for example, waking up at home and having breakfast at home should not be individual tasks).'
        f'Do not include any transportation or commuting tasks (for example, do not include actions like "walking to the station" or "driving a car" or "taking the bus"). '
        f'Do not mention vehicles or modes of transport in your task descriptions.\n'
        f'building options:\n{building_options}\n\n'
        f'{previous_day_text}\n'
        f'{daily_context_text}\n'
        f'Do not include any explanations, only provide a RFC8259 compliant JSON response following this format '
        f'without deviation.\n{{"description_of_today": '
        f'[{{"time":"HH:MM","action":"a one sentence description of what you start doing at that time", '
        f'"building_type": "building for your task which must be from above building options and can not be anything else"}},...]}}\n'
        f"Your response must be in English.\n"
        f'The JSON Response for {day}:\n'
    )
