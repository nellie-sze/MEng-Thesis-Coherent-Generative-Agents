library(rstudioapi)
setwd(dirname(getActiveDocumentContext()$path))

library(colorspace)
library(dplyr)
library(tidyr)
library(stringr)
library(readr)
library(ggplot2)
library(jsonlite)
library(scales)
library(easystats)
library(patchwork)
library(forcats)
library(lme4)
library(writexl)
library(tibble)
library(report)

colleyRstats::colleyRstats_setup()

dir.create("plots/uam", recursive = TRUE, showWarnings = FALSE)

COL_WALK      <- "#D96584"
COL_BICYCLE   <- "#C59600"
COL_PT        <- "#4FAA10"
COL_CAR       <- "#19ADA2"
COL_PT_DAY    <- "#1F95D0"
COL_PT_SINGLE <- "#B560D4"

q3 <- c(COL_PT, COL_CAR, COL_WALK)
q5 <- c(COL_WALK, COL_BICYCLE, COL_PT, COL_CAR, COL_PT_DAY)
UAM_INCOME_BUCKET_LEVELS <- c("Under EUR1.5k", "EUR1.5k-<EUR5k", "EUR5k+")

map_uam_income_bucket <- function(income_str) {
  case_when(
    income_str %in% c(
      "under 500 euros",
      "500 to under 900 euros",
      "900 to under 1,500 euros"
    ) ~ "Under EUR1.5k",
    income_str %in% c(
      "1,500 to under 2,000 euros",
      "2,000 to under 3,000 euros",
      "3,000 to under 4,000 euros",
      "4,000 to under 5,000 euros"
    ) ~ "EUR1.5k-<EUR5k",
    income_str %in% c(
      "5,000 to under 6,000 euros",
      "6,000 to 7,000 euros",
      "more than 7,000 euros"
    ) ~ "EUR5k+",
    TRUE ~ NA_character_
  )
}

# ── 1. Load data ─────────────────────────────────────────────────────────────

air_taxi <- read_csv("UAM/day1/air_taxi_options.csv", show_col_types = FALSE)
run_met  <- read_csv("UAM/day1/run_metrics.csv",      show_col_types = FALSE)
reasoning <- read_csv("UAM/day1/agent_reasoning.csv", show_col_types = FALSE)

air_taxi <- air_taxi |>
  mutate(
    chosen_bin = chosen == "yes",
    chosen_lbl = if_else(chosen_bin, "Air taxi chosen", "Air taxi rejected"),
    ranking_f  = factor(ranking)
  )

# ── 2. High-level acceptance summary ─────────────────────────────────────────

n_suggested <- run_met$air_taxi_suggested_count
n_chosen    <- run_met$air_taxi_chosen_count
acc_rate    <- n_chosen / n_suggested * 100

cat(sprintf(
  "\nAir-taxi summary:\n  Suggested: %d\n  Chosen:    %d\n  Acceptance rate: %.1f%%\n",
  n_suggested, n_chosen, acc_rate
))

# ── 3. Acceptance rate by speed ranking ───────────────────────────────────────

rank_acc <- air_taxi |>
  group_by(ranking_f) |>
  summarise(
    n_total  = n(),
    n_chosen = sum(chosen_bin),
    rate     = n_chosen / n_total * 100,
    .groups  = "drop"
  )

slice_palette <- function(palette_name, n, begin = 0, end = 1, base_n = 100) {
  full <- grDevices::colorRampPalette(c(COL_WALK, COL_BICYCLE, COL_PT, COL_CAR, COL_PT_DAY))(base_n)
  idx  <- round(seq(begin * (base_n - 1) + 1, end * (base_n - 1) + 1, length.out = n))
  full[idx]
}

p_rank <- rank_acc |>
  ggplot(aes(x = ranking_f, y = rate, fill = ranking_f)) +
  geom_col(width = 0.7, show.legend = FALSE) +
  geom_text(aes(label = sprintf("%.1f%%\n(n=%d)", rate, n_total)),
            vjust = -0.3, size = 3.8) +
  scale_fill_manual(values = setNames(rep(COL_PT_DAY, nlevels(rank_acc$ranking_f)),
                                      levels(rank_acc$ranking_f))) +
  scale_y_continuous(labels = label_percent(scale = 1),
                     expand = expansion(mult = c(0, 0.15))) +
  theme_lucid() +
  theme(axis.title = element_text(size = 13),
        axis.text  = element_text(size = 12)) +
  labs(x = "Air-taxi speed rank among available options (1 = fastest)",
       y = "Acceptance rate (%)",
       title = "Air-taxi acceptance rate by speed rank")

ggsave("plots/uam/01_acceptance_by_rank.pdf", p_rank, width = 10, height = 6, device = cairo_pdf)
ggsave("plots/uam/01_acceptance_by_rank.png", p_rank, width = 10, height = 6)

# ── 4. Fastest-gap distribution: chosen vs rejected ───────────────────────────

p_gap <- air_taxi |>
  filter(fastest_gap < quantile(fastest_gap, 0.99, na.rm = TRUE)) |>
  ggplot(aes(x = fastest_gap, fill = chosen_lbl, colour = chosen_lbl)) +
  geom_density(alpha = 0.35, linewidth = 1) +
  scale_fill_manual(  values = c("Air taxi chosen"   = q3[1],
                                 "Air taxi rejected" = q3[2]), name = NULL) +
  scale_color_manual( values = c("Air taxi chosen"   = q3[1],
                                 "Air taxi rejected" = q3[2]), name = NULL) +
  theme_lucid() +
  theme(legend.position = "inside",
        legend.position.inside = c(0.8, 0.85),
        axis.title = element_text(size = 13),
        axis.text  = element_text(size = 12)) +
  labs(x = "Minutes slower than fastest available option",
       y = "Density",
       title = "Time disadvantage of air taxi vs fastest alternative")

ggsave("plots/uam/02_fastest_gap_density.pdf", p_gap, width = 9, height = 6, device = cairo_pdf)
ggsave("plots/uam/02_fastest_gap_density.png", p_gap, width = 9, height = 6)

# ── 5. Fastest-ratio distribution: chosen vs rejected ────────────────────────

p_ratio <- air_taxi |>
  filter(fastest_ratio < quantile(fastest_ratio, 0.99, na.rm = TRUE)) |>
  ggplot(aes(x = fastest_ratio, fill = chosen_lbl, colour = chosen_lbl)) +
  geom_density(alpha = 0.35, linewidth = 1) +
  geom_vline(xintercept = 1, linetype = "dashed", color = "grey40") +
  annotate("text", x = 1.05, y = Inf, vjust = 1.5, hjust = 0,
           label = "Air taxi = fastest", size = 3.5, color = "grey40") +
  scale_fill_manual(  values = c("Air taxi chosen"   = q3[1],
                                 "Air taxi rejected" = q3[2]), name = NULL) +
  scale_color_manual( values = c("Air taxi chosen"   = q3[1],
                                 "Air taxi rejected" = q3[2]), name = NULL) +
  theme_lucid() +
  theme(legend.position = "inside",
        legend.position.inside = c(0.8, 0.85),
        axis.title = element_text(size = 13),
        axis.text  = element_text(size = 12)) +
  labs(x = "Air-taxi duration / fastest option duration (ratio)",
       y = "Density",
       title = "Speed ratio of air taxi relative to fastest alternative")

ggsave("plots/uam/03_fastest_ratio_density.pdf", p_ratio, width = 9, height = 6, device = cairo_pdf)
ggsave("plots/uam/03_fastest_ratio_density.png", p_ratio, width = 9, height = 6)

# ── 6. Air-taxi duration vs distance scatter, coloured by choice ──────────────

p_scatter <- air_taxi |>
  ggplot(aes(x = distance, y = duration, colour = chosen_lbl, alpha = chosen_bin)) +
  geom_point(size = 2) +
  scale_colour_manual(values = c("Air taxi chosen"   = q3[1],
                                 "Air taxi rejected" = q3[2]), name = NULL) +
  scale_alpha_manual(values = c("TRUE" = 0.9, "FALSE" = 0.25), guide = "none") +
  theme_lucid() +
  theme(legend.position = "inside",
        legend.position.inside = c(0.15, 0.9),
        axis.title = element_text(size = 13),
        axis.text  = element_text(size = 12)) +
  labs(x = "Air-taxi route distance (km)",
       y = "Air-taxi travel time (min)",
       title = "Air-taxi journey characteristics — chosen vs rejected")

ggsave("plots/uam/04_duration_vs_distance.pdf", p_scatter, width = 9, height = 6, device = cairo_pdf)
ggsave("plots/uam/04_duration_vs_distance.png", p_scatter, width = 9, height = 6)

# ── 7. Box: fastest_gap by rank ───────────────────────────────────────────────

p_gap_rank <- air_taxi |>
  filter(fastest_gap < quantile(fastest_gap, 0.99, na.rm = TRUE)) |>
  ggplot(aes(x = ranking_f, y = fastest_gap, fill = ranking_f)) +
  geom_boxplot(outlier.alpha = 0.3, show.legend = FALSE) +
  scale_fill_manual(values = slice_palette("Blues 3", nlevels(air_taxi$ranking_f),
                                           begin = 0.25, end = 0.85)) +
  theme_lucid() +
  theme(axis.title = element_text(size = 13),
        axis.text  = element_text(size = 12)) +
  labs(x = "Speed rank (1 = fastest available)",
       y = "Minutes slower than fastest option",
       title = "Time disadvantage of air taxi by speed rank")

ggsave("plots/uam/05_gap_by_rank.pdf", p_gap_rank, width = 9, height = 6, device = cairo_pdf)
ggsave("plots/uam/05_gap_by_rank.png", p_gap_rank, width = 9, height = 6)

# ── 8. Acceptance rate by fastest_gap bin ────────────────────────────────────

air_taxi_binned <- air_taxi |>
  mutate(gap_bin = cut(fastest_gap,
                       breaks = c(-Inf, 0, 5, 15, 30, 60, Inf),
                       labels = c("≤0 (fastest)", "0–5", "5–15", "15–30", "30–60", ">60"),
                       right  = TRUE))

bin_acc <- air_taxi_binned |>
  group_by(gap_bin) |>
  summarise(
    n_total  = n(),
    n_chosen = sum(chosen_bin),
    rate     = n_chosen / n_total * 100,
    .groups  = "drop"
  ) |>
  filter(!is.na(gap_bin))

p_gap_acc <- bin_acc |>
  ggplot(aes(x = gap_bin, y = rate)) +
  geom_col(fill = q3[1], width = 0.7) +
  geom_text(aes(label = sprintf("%.1f%%\n(n=%d)", rate, n_total)),
            vjust = -0.3, size = 3.8) +
  scale_y_continuous(labels = label_percent(scale = 1),
                     expand = expansion(mult = c(0, 0.18))) +
  theme_lucid() +
  theme(axis.title = element_text(size = 13),
        axis.text  = element_text(size = 11)) +
  labs(x = "Minutes slower than fastest alternative",
       y = "Acceptance rate (%)",
       title = "Air-taxi acceptance rate by time disadvantage (binned)")

ggsave("plots/uam/06_acceptance_by_gap_bin.pdf", p_gap_acc, width = 10, height = 6, device = cairo_pdf)
ggsave("plots/uam/06_acceptance_by_gap_bin.png", p_gap_acc, width = 10, height = 6)

air_taxi_distance_binned <- air_taxi |>
  mutate(distance_bin = cut(distance,
                            breaks = c(3, 4, 5, 6, 7, Inf),
                            labels = c("3â€“4 km", "4â€“5 km", "5â€“6 km", "6â€“7 km", ">7 km"),
                            right  = FALSE))

distance_acc <- air_taxi_distance_binned |>
  group_by(distance_bin) |>
  summarise(
    n_total  = n(),
    n_chosen = sum(chosen_bin),
    rate     = n_chosen / n_total * 100,
    .groups  = "drop"
  ) |>
  filter(!is.na(distance_bin))

p_distance_acc <- distance_acc |>
  ggplot(aes(x = distance_bin, y = rate)) +
  geom_col(fill = q3[2], width = 0.7) +
  geom_text(aes(label = sprintf("%.1f%%\n(n=%d)", rate, n_total)),
            vjust = -0.3, size = 3.8) +
  scale_y_continuous(labels = label_percent(scale = 1),
                     expand = expansion(mult = c(0, 0.18))) +
  theme_lucid() +
  theme(axis.title = element_text(size = 13),
        axis.text  = element_text(size = 11)) +
  labs(x = "Air-taxi journey distance",
       y = "Acceptance rate (%)",
       title = "Air-taxi acceptance rate by journey distance (binned)")

ggsave("plots/uam/06b_acceptance_by_distance_bin.pdf", p_distance_acc, width = 10, height = 6, device = cairo_pdf)
ggsave("plots/uam/06b_acceptance_by_distance_bin.png", p_distance_acc, width = 10, height = 6)

# ── 9. Modal split in UAM scenario (all legs) ────────────────────────────────

# -- 8c. Distribution of accepted air-taxi trip counts per agent ---------------

uam_trip_freq <- air_taxi |>
  group_by(agentID) |>
  summarise(n_air_taxi_used = sum(chosen_bin), .groups = "drop") |>
  filter(n_air_taxi_used > 0) |>
  count(n_air_taxi_used, name = "n_agents") |>
  arrange(n_air_taxi_used)

p_trip_freq <- uam_trip_freq |>
  mutate(n_air_taxi_used_f = factor(n_air_taxi_used, levels = n_air_taxi_used)) |>
  ggplot(aes(x = n_air_taxi_used_f, y = n_agents)) +
  geom_col(width = 0.7, fill = COL_PT_DAY) +
  geom_text(aes(label = n_agents), vjust = -0.3, size = 3.8) +
  scale_y_continuous(expand = expansion(mult = c(0, 0.15))) +
  theme_lucid() +
  theme(axis.title = element_text(size = 13),
        axis.text = element_text(size = 11)) +
  labs(x = "Accepted air-taxi trips per agent",
       y = "Number of agents",
       title = "Number of agents by accepted air-taxi trip count")

ggsave("plots/uam/06c_air_taxi_trip_count_distribution.pdf", p_trip_freq, width = 9, height = 6, device = cairo_pdf)
ggsave("plots/uam/06c_air_taxi_trip_count_distribution.png", p_trip_freq, width = 9, height = 6)
reasoning <- reasoning |>
  mutate(
    mot_choice = str_replace_all(str_trim(mot_choice), "\\s+", "_"),
    mode_group = case_when(
      mot_choice %in% c("pedestrian", "walk")                                              ~ "Walk",
      mot_choice %in% c("personal_bicycle", "bicycle",
                        "rental_bicycle", "one_use_bike", "single-use_bicycle")            ~ "Bicycle",
      mot_choice %in% c("personal_car", "passenger",
                        "road_taxi", "taxi", "rental_car")                                 ~ "Car",
      mot_choice %in% c("PT_single", "public_transport_single",
                        "public_transport_single_ticket",
                        "PT_day_pass", "public_transport_day",
                        "public_transport_day_pass",
                        "PT_monthly_pass", "public_transport_monthly",
                        "public_transport_monthly_pass", "9_euro_PT_monthly_pass")         ~ "Public transport",
      mot_choice %in% c("air_taxi", "uam")                                                 ~ "Air taxi",
      TRUE ~ NA_character_
    )
  ) |>
  filter(!is.na(mode_group), mot_choice != "")

modal_uam <- reasoning |>
  count(mode_group) |>
  mutate(pct = n / sum(n) * 100,
         mode_group = fct_reorder(mode_group, n, .desc = TRUE))

mode_colors_uam <- c(
  "Walk" = COL_WALK,
  "Bicycle" = COL_BICYCLE,
  "Public transport" = COL_PT,
  "Car" = COL_CAR,
  "Air taxi" = COL_PT_DAY
)
mode_colors_uam <- mode_colors_uam[levels(modal_uam$mode_group)]

p_modal_uam <- modal_uam |>
  ggplot(aes(x = mode_group, y = pct, fill = mode_group)) +
  geom_col(width = 0.7, show.legend = FALSE) +
  geom_text(aes(label = sprintf("%.1f%%", pct)), vjust = -0.4, size = 4) +
  scale_fill_manual(values = mode_colors_uam) +
  scale_y_continuous(labels = label_percent(scale = 1),
                     expand = expansion(mult = c(0, 0.12))) +
  theme_lucid() +
  theme(axis.text.x  = element_text(angle = 30, hjust = 1, size = 11),
        axis.title   = element_text(size = 13)) +
  labs(x = NULL, y = "Share of legs (%)",
       title = "Overall modal split — UAM scenario (day 1)")

ggsave("plots/uam/07_modal_split.pdf", p_modal_uam, width = 10, height = 6, device = cairo_pdf)
ggsave("plots/uam/07_modal_split.png", p_modal_uam, width = 10, height = 6)

# ── 10. Personas: air-taxi choice by agent attributes ────────────────────────

personas_uam <- tryCatch({
  raw <- fromJSON("UAM/day1/agents_1_description.json", simplifyDataFrame = FALSE)
  lapply(seq_along(raw), function(i) {
    ag <- fromJSON(raw[[i]], simplifyDataFrame = FALSE)
    vehicle_names <- if (length(ag$owned_vehicles) > 0) {
      Filter(Negate(is.na), vapply(
        ag$owned_vehicles,
        function(v) as.character(v$name %||% NA_character_),
        character(1)
      ))
    } else {
      character(0)
    }
    data.frame(
      agent_id        = as.integer(ag$agent_id %||% ag$id %||% (i - 1)),
      occupation      = as.character(ag$occupation %||% ag$seed$attributes[["Person's occupation"]] %||% NA),
      monthly_income  = as.character(ag$seed$attributes[["Monthly net household income"]] %||% NA),
      economic_status = map_uam_income_bucket(as.character(
        ag$seed$attributes[["Monthly net household income"]] %||% NA_character_
      )),
      age             = as.integer(ag$age %||% ag$seed$attributes[["Age"]] %||% NA),
      has_car         = "personal_car" %in% vehicle_names,
      has_bicycle     = "personal_bicycle" %in% vehicle_names,
      has_monthly_pass = as.logical(ag$travel_pass %||% FALSE),
      stringsAsFactors = FALSE
    )
  }) |> bind_rows()
}, error = function(e) { message("Persona JSON parse failed: ", e$message); NULL })

if (!is.null(personas_uam)) {

  # Tag agents who chose air taxi at least once
  agents_chose_uam <- air_taxi |>
    filter(chosen_bin) |>
    distinct(agentID) |>
    rename(agent_id = agentID)

  agents_offered_uam <- air_taxi |>
    distinct(agentID) |>
    rename(agent_id = agentID)

  persona_uam_join <- personas_uam |>
    filter(agent_id %in% agents_offered_uam$agent_id) |>
    mutate(
      chose_air_taxi = agent_id %in% agents_chose_uam$agent_id,
      economic_status = factor(economic_status, levels = UAM_INCOME_BUCKET_LEVELS)
    )

  if ("economic_status" %in% names(persona_uam_join)) {
    econ_acc <- persona_uam_join |>
      group_by(economic_status) |>
      summarise(
        n_agents  = n(),
        n_chose   = sum(chose_air_taxi),
        rate      = n_chose / n_agents * 100,
        .groups   = "drop"
      )

    p_econ_acc <- econ_acc |>
      mutate(economic_status = factor(economic_status, levels = UAM_INCOME_BUCKET_LEVELS)) |>
      ggplot(aes(x = economic_status, y = rate, fill = economic_status)) +
      geom_col(width = 0.65, show.legend = FALSE) +
      geom_text(aes(label = sprintf("%.1f%%\n(n=%d)", rate, n_agents)),
                vjust = -0.3, size = 3.8) +
      scale_fill_manual(values = c(
        "Under EUR1.5k" = COL_WALK,
        "EUR1.5k-<EUR5k" = COL_BICYCLE,
        "EUR5k+" = COL_PT
      )) +
      scale_y_continuous(labels = label_percent(scale = 1),
                         expand = expansion(mult = c(0, 0.15))) +
      theme_lucid() +
      theme(axis.text.x  = element_text(angle = 20, hjust = 1),
            axis.title   = element_text(size = 13)) +
      labs(x = "Monthly net household income", y = "% of offered agents who chose air taxi",
           title = "Air-taxi adoption by household income bucket")

    ggsave("plots/uam/08_adoption_by_econ_status.pdf", p_econ_acc, width = 9, height = 6, device = cairo_pdf)
    ggsave("plots/uam/08_adoption_by_econ_status.png", p_econ_acc, width = 9, height = 6)
  }

  age_acc <- air_taxi |>
    rename(agent_id = agentID) |>
    left_join(personas_uam |> select(agent_id, age), by = "agent_id") |>
    filter(!is.na(age)) |>
    mutate(age_bin = cut(age,
                         breaks = c(-Inf, 24, 39, 54, 69, Inf),
                         labels = c("Under 25", "25-39", "40-54", "55-69", "70+"),
                         right = TRUE)) |>
    group_by(age_bin) |>
    summarise(
      n_total  = n(),
      n_chosen = sum(chosen_bin),
      rate     = n_chosen / n_total * 100,
      .groups  = "drop"
    ) |>
    filter(!is.na(age_bin))

  p_age_acc <- age_acc |>
    ggplot(aes(x = age_bin, y = rate)) +
    geom_col(fill = COL_PT_SINGLE, width = 0.7) +
    geom_text(aes(label = sprintf("%.1f%%\n(n=%d)", rate, n_total)),
              vjust = -0.3, size = 3.8) +
    scale_y_continuous(labels = label_percent(scale = 1),
                       expand = expansion(mult = c(0, 0.18))) +
    theme_lucid() +
    theme(axis.title = element_text(size = 13),
          axis.text  = element_text(size = 11)) +
    labs(x = "Age group",
         y = "Acceptance rate (%)",
         title = "Air-taxi acceptance rate by age (binned)")

  ggsave("plots/uam/08b_acceptance_by_age_bin.pdf", p_age_acc, width = 10, height = 6, device = cairo_pdf)
  ggsave("plots/uam/08b_acceptance_by_age_bin.png", p_age_acc, width = 10, height = 6)

  ownership_summary <- air_taxi |>
    rename(agent_id = agentID) |>
    left_join(personas_uam |> select(agent_id, has_car, has_bicycle, has_monthly_pass), by = "agent_id") |>
    mutate(
      has_car = coalesce(has_car, FALSE),
      has_bicycle = coalesce(has_bicycle, FALSE),
      has_monthly_pass = coalesce(has_monthly_pass, FALSE),
      none_owner = !has_car & !has_bicycle & !has_monthly_pass
    ) |>
    summarise(
      `Personal car owners` = mean(chosen_bin[has_car]) * 100,
      `Bicycle owners` = mean(chosen_bin[has_bicycle]) * 100,
      `Monthly pass owners` = mean(chosen_bin[has_monthly_pass]) * 100,
      `None owners` = mean(chosen_bin[none_owner]) * 100,
      `n_car` = sum(has_car),
      `n_bicycle` = sum(has_bicycle),
      `n_pass` = sum(has_monthly_pass),
      `n_none` = sum(none_owner)
    )

  ownership_acc <- tibble(
    owner_group = c("Personal car owners", "Bicycle owners", "Monthly pass owners", "None owners"),
    rate = c(
      ownership_summary$`Personal car owners`,
      ownership_summary$`Bicycle owners`,
      ownership_summary$`Monthly pass owners`,
      ownership_summary$`None owners`
    ),
    n_total = c(
      ownership_summary$`n_car`,
      ownership_summary$`n_bicycle`,
      ownership_summary$`n_pass`,
      ownership_summary$`n_none`
    )
  )

  p_owner_acc <- ownership_acc |>
    mutate(owner_group = factor(owner_group,
                                levels = c("Personal car owners", "Bicycle owners",
                                           "Monthly pass owners", "None owners"))) |>
    ggplot(aes(x = owner_group, y = rate, fill = owner_group)) +
    geom_col(width = 0.7, show.legend = FALSE) +
    geom_text(aes(label = sprintf("%.1f%%\n(n=%d)", rate, n_total)),
              vjust = -0.3, size = 3.8) +
    scale_fill_manual(values = c(
      "Personal car owners" = COL_CAR,
      "Bicycle owners" = COL_BICYCLE,
      "Monthly pass owners" = COL_PT,
      "None owners" = COL_WALK
    )) +
    scale_y_continuous(labels = label_percent(scale = 1),
                       expand = expansion(mult = c(0, 0.18))) +
    theme_lucid() +
    theme(axis.title = element_text(size = 13),
          axis.text.x = element_text(angle = 20, hjust = 1, size = 11),
          axis.text.y = element_text(size = 11)) +
    labs(x = NULL,
         y = "Acceptance rate (%)",
         title = "Air-taxi acceptance rate by ownership group")

  ggsave("plots/uam/08c_acceptance_by_ownership_group.pdf", p_owner_acc, width = 10, height = 6, device = cairo_pdf)
  ggsave("plots/uam/08c_acceptance_by_ownership_group.png", p_owner_acc, width = 10, height = 6)

} else {
  message("Skipping persona-based UAM plots.")
}

# ── 11. Inconsistency analysis ────────────────────────────────────────────────

uam_inc <- read_csv("UAM/day1/inconsistencies.csv", show_col_types = FALSE)

if (nrow(uam_inc) > 0) {
  uam_inc <- uam_inc |>
    mutate(
      rule_label = case_when(
        trigger_rule == "ownership"               ~ "Ownership",
        trigger_rule == "route_availability"      ~ "Route availability",
        trigger_rule %in% c("first_use_location",
                            "activation")         ~ "First-use location",
        trigger_rule == "home_consistency_return" ~ "Home return",
        TRUE ~ str_to_title(str_replace_all(trigger_rule, "_", " "))
      )
    )

  p_uam_inc <- uam_inc |>
    count(rule_label) |>
    mutate(rule_label = fct_reorder(rule_label, n)) |>
    ggplot(aes(x = rule_label, y = n, fill = rule_label)) +
    geom_col(width = 0.65, show.legend = FALSE) +
    geom_text(aes(label = n), hjust = -0.2, size = 4) +
    coord_flip() +
    scale_fill_manual(values = c(
      "Route availability" = COL_WALK,
      "First-use location" = COL_BICYCLE,
      "Home return" = COL_PT,
      "Ownership" = COL_CAR
    )) +
    scale_y_continuous(expand = expansion(mult = c(0, 0.15))) +
    theme_lucid() +
    theme(axis.title = element_text(size = 13)) +
    labs(x = NULL, y = "Count", title = "Inconsistency types — UAM scenario")

  ggsave("plots/uam/09_inconsistency_types.pdf", p_uam_inc, width = 9, height = 5, device = cairo_pdf)
  ggsave("plots/uam/09_inconsistency_types.png", p_uam_inc, width = 9, height = 5)
}

# ── 12. Combined acceptance summary panel ─────────────────────────────────────

p_combined <- (p_rank + p_gap_acc) +
  plot_annotation(
    title   = "Air-taxi acceptance: speed rank vs time disadvantage",
    theme   = theme(plot.title = element_text(size = 15, face = "bold"))
  )

ggsave("plots/uam/10_acceptance_summary_panel.pdf", p_combined, width = 16, height = 6, device = cairo_pdf)
ggsave("plots/uam/10_acceptance_summary_panel.png", p_combined, width = 16, height = 6)

# ════════════════════════════════════════════════════════════════════════════
# STATISTICAL TESTS
# ════════════════════════════════════════════════════════════════════════════

# ── S1. Chi-square: acceptance varies by speed rank? ─────────────────────────

rank_ct <- air_taxi |>
  count(ranking_f, chosen_bin) |>
  pivot_wider(names_from = chosen_bin, values_from = n, values_fill = 0,
              names_prefix = "chosen_") |>
  column_to_rownames("ranking_f") |>
  as.matrix()

chisq_rank <- chisq.test(rank_ct)
cat("\n── Chi-square: acceptance varies by speed rank ──\n")
print(chisq_rank)
cat(sprintf("Cramér's V = %.3f\n",
            sqrt(chisq_rank$statistic / (sum(rank_ct) * (min(dim(rank_ct)) - 1)))))

# ── S2. Logistic regression: chosen ~ fastest_gap + ranking + distance ────────
# Route-level: one row per air-taxi option offered.

air_taxi_glm <- air_taxi |>
  mutate(
    ranking_num = as.integer(as.character(ranking_f)),
    chosen_int  = as.integer(chosen_bin)
  )

glm_uam <- glm(
  chosen_int ~ fastest_gap + ranking_num + distance,
  data   = air_taxi_glm,
  family = binomial
)

cat("\n── Logistic regression: chosen ~ fastest_gap + ranking + distance ──\n")
print(report(glm_uam))
print(report_table(glm_uam))

or_uam <- cbind(
  OR      = exp(coef(glm_uam)),
  exp(confint(glm_uam))
) |>
  as.data.frame() |>
  rownames_to_column("term") |>
  rename(CI_low = `2.5 %`, CI_high = `97.5 %`) |>
  mutate(across(c(OR, CI_low, CI_high), ~round(.x, 4)))

cat("\nOdds ratios:\n")
print(or_uam)

# ── S3. Mixed logistic with agent random effect ───────────────────────────────

cat("\n── Mixed logistic: chosen ~ fastest_gap + ranking + distance + (1|agentID) ──\n")
glmer_uam <- glmer(
  chosen_int ~ fastest_gap + ranking_num + distance + (1 | agentID),
  data    = air_taxi_glm,
  family  = binomial,
  control = glmerControl(optimizer = "bobyqa")
)
cat("\nMixed logistic report:\n")
print(report(glmer_uam))
print(report_table(glmer_uam))

if (lme4::isSingular(glmer_uam))
  message("⚠ glmer_uam: singular fit — check random-effects structure")

cat("\nLRT: random-effects model vs fixed-effects only:\n")
print(anova(glm_uam, glmer_uam))

or_uam_mixed <- data.frame(
  OR      = exp(fixef(glmer_uam)),
  CI_low  = exp(confint(glmer_uam, parm = "beta_", method = "Wald")[, 1]),
  CI_high = exp(confint(glmer_uam, parm = "beta_", method = "Wald")[, 2])
) |>
  rownames_to_column("term") |>
  mutate(across(c(OR, CI_low, CI_high), ~round(.x, 4)))

cat("\nOdds ratios (mixed model):\n")
print(or_uam_mixed)

# ── S4. Kruskal-Wallis: fastest_gap differs between chosen / rejected ──────────

kw_gap <- kruskal.test(fastest_gap ~ chosen_bin, data = air_taxi_glm)
cat(sprintf("\n── Kruskal-Wallis: fastest_gap chosen vs rejected ──\n"))
print(kw_gap)

kw_ratio <- kruskal.test(fastest_ratio ~ chosen_bin, data = air_taxi_glm)
cat(sprintf("\n── Kruskal-Wallis: fastest_ratio chosen vs rejected ──\n"))
print(kw_ratio)

# Effect size r = Z / sqrt(N)
z_gap   <- qnorm(kw_gap$p.value   / 2)
z_ratio <- qnorm(kw_ratio$p.value / 2)
cat(sprintf("Effect size r (gap)   = %.3f\n", abs(z_gap)   / sqrt(nrow(air_taxi_glm))))
cat(sprintf("Effect size r (ratio) = %.3f\n", abs(z_ratio) / sqrt(nrow(air_taxi_glm))))

# ── S5. Economic status × acceptance (chi-square, if persona data available) ──

econ_chi_result <- NULL
if (!is.null(personas_uam) && "economic_status" %in% names(personas_uam)) {
  econ_air <- air_taxi_glm |>
    rename(agent_id = agentID) |>
    left_join(personas_uam |> select(agent_id, economic_status), by = "agent_id") |>
    filter(!is.na(economic_status)) |>
    mutate(economic_status = factor(economic_status, levels = UAM_INCOME_BUCKET_LEVELS))

  econ_ct <- econ_air |>
    count(economic_status, chosen_bin) |>
    pivot_wider(names_from = chosen_bin, values_from = n, values_fill = 0,
                names_prefix = "chosen_") |>
    column_to_rownames("economic_status") |>
    as.matrix()

  chisq_econ <- chisq.test(econ_ct)
  cat("\n── Chi-square: acceptance varies by household income bucket ──\n")
  print(chisq_econ)
  econ_chi_result <- tibble(chi_sq = chisq_econ$statistic,
                            df     = chisq_econ$parameter,
                            p      = chisq_econ$p.value)
}

# ── Export ────────────────────────────────────────────────────────────────────


age_chi_result <- NULL
if (!is.null(personas_uam) && "age" %in% names(personas_uam)) {
  age_air <- air_taxi_glm |>
    rename(agent_id = agentID) |>
    left_join(personas_uam |> select(agent_id, age), by = "agent_id") |>
    filter(!is.na(age)) |>
    mutate(age_bin = cut(age,
                         breaks = c(-Inf, 24, 39, 54, 69, Inf),
                         labels = c("Under 25", "25-39", "40-54", "55-69", "70+"),
                         right = TRUE)) |>
    filter(!is.na(age_bin))

  age_ct <- age_air |>
    count(age_bin, chosen_bin) |>
    pivot_wider(names_from = chosen_bin, values_from = n, values_fill = 0,
                names_prefix = "chosen_") |>
    column_to_rownames("age_bin") |>
    as.matrix()

  chisq_age <- chisq.test(age_ct)
  cat("\n── Chi-square: acceptance varies by age group ──\n")
  print(chisq_age)
  age_chi_result <- tibble(chi_sq = chisq_age$statistic,
                           df     = chisq_age$parameter,
                           p      = chisq_age$p.value)
}
write_xlsx(
  list(
    chisq_by_rank       = tibble(chi_sq = chisq_rank$statistic,
                                 df     = chisq_rank$parameter,
                                 p      = chisq_rank$p.value),
    kruskal_gap         = tibble(kw_stat = kw_gap$statistic,   p = kw_gap$p.value),
    kruskal_ratio       = tibble(kw_stat = kw_ratio$statistic, p = kw_ratio$p.value),
    logistic_OR_fixed   = or_uam,
    logistic_OR_mixed   = or_uam_mixed,
    chisq_econ_status   = if (!is.null(econ_chi_result)) econ_chi_result
                          else tibble(note = "No persona data"),
    chisq_age_group     = if (!is.null(age_chi_result)) age_chi_result
                          else tibble(note = "No persona data")
  ),
  "plots/uam/uam_stats.xlsx"
)

# ── HTML diagnostic dashboards (run last so console output doesn't interleave) ─

cat("\nGenerating model dashboards (HTML)…\n")
invisible(capture.output(suppressMessages({
  try(easystats::model_dashboard(glm_uam,
        output_file = "plots/uam/glm_uam_dashboard.html"), silent = TRUE)
  try(easystats::model_dashboard(glmer_uam,
        output_file = "plots/uam/glmer_uam_dashboard.html"), silent = TRUE)
})))

cat("\nUAM analysis complete. Plots written to plots/uam/\n")


