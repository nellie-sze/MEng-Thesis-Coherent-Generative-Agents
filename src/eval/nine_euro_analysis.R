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

dir.create("plots/nine_euro", recursive = TRUE, showWarnings = FALSE)

COL_WALK      <- "#D96584"
COL_BICYCLE   <- "#C59600"
COL_PT        <- "#4FAA10"
COL_CAR       <- "#19ADA2"
COL_PT_DAY    <- "#1F95D0"
COL_PT_SINGLE <- "#B560D4"

q6  <- c(COL_WALK, COL_BICYCLE, COL_PT, COL_CAR, COL_PT_DAY, COL_PT_SINGLE)
q3  <- c(COL_PT, COL_CAR, COL_WALK)

DAY_LABELS <- c("1" = "Day 1\n(Mon – full price)",
                "2" = "Day 2\n(Sat – 9-Euro)",
                "3" = "Day 3\n(Tue – 9-Euro)")

PT_MODES <- c("PT_single", "PT_day_pass", "PT_monthly_pass", "9_euro_PT_monthly_pass",
              "public_transport_single", "public_transport_day", "public_transport_monthly")
INCOME_BUCKET_LEVELS <- c("0-<1500", "1500-<4000", ">=4000")

map_income_bucket <- function(income_str) {
  case_when(
    income_str %in% c(
      "under 500 euros",
      "500 to under 900 euros",
      "900 to under 1,500 euros"
    ) ~ "0-<1500",
    income_str %in% c(
      "1,500 to under 2,000 euros",
      "2,000 to under 3,000 euros",
      "3,000 to under 4,000 euros"
    ) ~ "1500-<4000",
    income_str %in% c(
      "4,000 to under 5,000 euros",
      "5,000 to under 6,000 euros",
      "6,000 to 7,000 euros",
      "more than 7,000 euros"
    ) ~ ">=4000",
    TRUE ~ NA_character_
  )
}

# ── 1. Load agent_reasoning for all days ─────────────────────────────────────

load_reasoning <- function(day) {
  path <- file.path("9 Euro", paste0("day", day), "agent_reasoning.csv")
  df   <- read_csv(path, show_col_types = FALSE)
  df$day <- day
  df
}

reasoning <- bind_rows(lapply(1:3, load_reasoning))

reasoning <- reasoning |>
  mutate(
    mot_choice   = str_replace_all(str_trim(mot_choice), "\\s+", "_"),
    is_consistent = inconsistencies == "Consistent" | is.na(inconsistencies),
    mode_group = case_when(
      mot_choice %in% c("pedestrian", "walk")                                              ~ "Walk",
      mot_choice %in% c("personal_bicycle", "bicycle",
                        "rental_bicycle", "one_use_bike", "single-use_bicycle")            ~ "Bicycle",
      mot_choice %in% c("personal_car", "passenger",
                        "road_taxi", "taxi", "rental_car")                                 ~ "Car",
      mot_choice %in% c("PT_single", "public_transport_single",
                        "public_transport_single_ticket")                                  ~ "PT single",
      mot_choice %in% c("PT_day_pass", "public_transport_day",
                        "public_transport_day_pass")                                       ~ "PT day pass",
      mot_choice %in% c("PT_monthly_pass", "public_transport_monthly",
                        "public_transport_monthly_pass", "9_euro_PT_monthly_pass")         ~ "PT monthly pass",
      TRUE ~ NA_character_
    ),
    is_pt = mode_group %in% c("PT single", "PT day pass", "PT monthly pass")
  ) |>
  filter(!is.na(mode_group), mot_choice != "")

# ── 2. Load agent personas (occupation, economic status) ──────────────────────

load_personas <- function(day) {
  path <- file.path("9 Euro", paste0("day", day), "agents_1_description.json")
  raw  <- fromJSON(path, simplifyDataFrame = FALSE)

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
      income_bucket   = map_income_bucket(as.character(
        ag$seed$attributes[["Monthly net household income"]] %||% NA_character_
      )),
      age             = as.integer(ag$age %||% ag$seed$attributes[["Age"]] %||% NA),
      has_car         = "personal_car" %in% vehicle_names,
      has_bicycle     = "personal_bicycle" %in% vehicle_names,
      stringsAsFactors = FALSE
    )
  }) |> bind_rows()
}

personas <- tryCatch(load_personas(1), error = function(e) {
  message("Could not parse persona JSON: ", e$message,
          "\n  Socioeconomic breakdowns will be skipped.")
  NULL
})

if (!is.null(personas)) {
  reasoning <- left_join(reasoning, personas, by = "agent_id")
}

# ── 3. Modal split per day ────────────────────────────────────────────────────

modal_by_day <- reasoning |>
  filter(!is.na(mode_group)) |>
  count(day, mode_group) |>
  group_by(day) |>
  mutate(pct = n / sum(n) * 100) |>
  ungroup() |>
  mutate(
    day = factor(day),
    mode_group = fct_reorder(mode_group, n, .desc = TRUE)
  )

mode_colors <- c(
  "Walk" = COL_WALK,
  "Bicycle" = COL_BICYCLE,
  "Personal bicycle" = COL_BICYCLE,
  "Rental bicycle" = "#E0B84A",
  "Car" = COL_CAR,
  "Personal car" = COL_CAR,
  "Taxi" = "#3DB8AE",
  "Rental car" = "#6CCFC7",
  "PT monthly pass" = COL_PT,
  "PT day pass" = COL_PT_DAY,
  "PT single" = COL_PT_SINGLE
)
mode_colors <- mode_colors[levels(modal_by_day$mode_group)]

p_modal_day <- modal_by_day |>
  ggplot(aes(x = day, y = pct, fill = mode_group)) +
  geom_col(width = 0.7, position = "stack") +
  scale_fill_manual(values = mode_colors, name = "Mode") +
  scale_x_discrete(labels = DAY_LABELS) +
  scale_y_continuous(labels = label_percent(scale = 1),
                     expand = expansion(mult = c(0, 0.02))) +
  theme_lucid() +
  theme(legend.position = "right",
        axis.title = element_text(size = 13),
        axis.text  = element_text(size = 12)) +
  labs(x = NULL, y = "Share of legs (%)",
       title = "Modal split across simulation days (9-Euro ticket scenario)")

ggsave("plots/nine_euro/01_modal_split_by_day.pdf", p_modal_day, width = 10, height = 6, device = cairo_pdf)
ggsave("plots/nine_euro/01_modal_split_by_day.png", p_modal_day, width = 10, height = 6)
modal_comparison_sim <- reasoning |>
  filter(!is.na(mode_group), !is.na(distance_km)) |>
  mutate(
    comparison_mode = case_when(
      mode_group == "Walk" ~ "Walk",
      mode_group == "Bicycle" ~ "Bicycle",
      mode_group == "Car" ~ "Car",
      str_starts(mode_group, "PT ") ~ "Public transport",
      TRUE ~ NA_character_
    )
  ) |>
  filter(!is.na(comparison_mode)) |>
  group_by(day, comparison_mode) |>
  summarise(distance_km = sum(as.numeric(distance_km), na.rm = TRUE), .groups = "drop") |>
  group_by(day) |>
  mutate(pct = distance_km / sum(distance_km) * 100) |>
  ungroup() |>
  mutate(period = if_else(day == 1, "Before", "During")) |>
  group_by(period, comparison_mode) |>
  summarise(
    pct = if (first(period) == "Before") first(pct) else mean(pct),
    .groups = "drop"
  ) |>
  mutate(
    source = "Simulated",
    comparison_mode = factor(comparison_mode,
                             levels = c("Walk", "Bicycle", "Public transport", "Car")),
    period = factor(period, levels = c("Before", "During"))
  )

modal_comparison_observed <- tribble(
  ~period,   ~comparison_mode,    ~pct,
  "Before", "Car",               50,
  "Before", "Bicycle",           7,
  "Before", "Public transport",  38,
  "Before", "Walk",              5,
  "During", "Car",               43,
  "During", "Bicycle",           8,
  "During", "Public transport",  45,
  "During", "Walk",              4
) |>
  mutate(
    source = "Observed",
    comparison_mode = factor(comparison_mode,
                             levels = c("Walk", "Bicycle", "Public transport", "Car")),
    period = factor(period, levels = c("Before", "During"))
  )

modal_comparison_df <- bind_rows(modal_comparison_sim, modal_comparison_observed) |>
  mutate(source = factor(source, levels = c("Observed", "Simulated")))

p_modal_comparison <- modal_comparison_df |>
  ggplot(aes(x = period, y = pct, fill = comparison_mode)) +
  geom_col(width = 0.7, position = "stack") +
  facet_wrap(~source, ncol = 2) +
  scale_fill_manual(
    values = c(
      "Walk" = "#D96584",
      "Bicycle" = "#C59600",
      "Public transport" = "#4FAA10",
      "Car" = "#19ADA2"
    ),
    name = "Mode"
  ) +
  scale_y_continuous(labels = label_percent(scale = 1),
                     expand = expansion(mult = c(0, 0.02))) +
  theme_lucid() +
  theme(axis.title = element_text(size = 13),
        axis.text = element_text(size = 12),
        strip.text = element_text(size = 12),
        legend.position = "right") +
  labs(x = NULL, y = "Mode share (%)",
       title = "Normalised modal split comparison")

ggsave("plots/nine_euro/01b_modal_split_comparison.pdf", p_modal_comparison, width = 12, height = 6, device = cairo_pdf)
ggsave("plots/nine_euro/01b_modal_split_comparison.png", p_modal_comparison, width = 12, height = 6)
modal_comparison_raw_sim <- reasoning |>
  filter(!is.na(mode_group)) |>
  mutate(
    comparison_mode = case_when(
      mode_group == "Walk" ~ "Walk",
      mode_group == "Bicycle" ~ "Bicycle",
      mode_group == "Car" ~ "Car",
      str_starts(mode_group, "PT ") ~ "Public transport",
      TRUE ~ NA_character_
    )
  ) |>
  filter(!is.na(comparison_mode)) |>
  count(day, comparison_mode) |>
  group_by(day) |>
  mutate(pct = n / sum(n) * 100) |>
  ungroup() |>
  mutate(period = if_else(day == 1, "Before", "During")) |>
  group_by(period, comparison_mode) |>
  summarise(
    pct = if (first(period) == "Before") first(pct) else mean(pct),
    .groups = "drop"
  ) |>
  mutate(
    source = "Simulated",
    comparison_mode = factor(comparison_mode,
                             levels = c("Walk", "Bicycle", "Public transport", "Car")),
    period = factor(period, levels = c("Before", "During"))
  )

modal_comparison_raw_observed <- tribble(
  ~period,   ~comparison_mode,    ~pct,
  "Before", "Car",               60.69,
  "Before", "Bicycle",           15.91,
  "Before", "Public transport",  11.81,
  "Before", "Walk",              11.59,
  "During", "Car",               58.83,
  "During", "Bicycle",           17.25,
  "During", "Public transport",  12.14,
  "During", "Walk",              11.78
) |>
  mutate(
    source = "Observed",
    comparison_mode = factor(comparison_mode,
                             levels = c("Walk", "Bicycle", "Public transport", "Car")),
    period = factor(period, levels = c("Before", "During"))
  )

modal_comparison_raw_df <- bind_rows(modal_comparison_raw_sim, modal_comparison_raw_observed) |>
  mutate(source = factor(source, levels = c("Observed", "Simulated")))

p_modal_comparison_raw <- modal_comparison_raw_df |>
  ggplot(aes(x = period, y = pct, fill = comparison_mode)) +
  geom_col(width = 0.7, position = "stack") +
  geom_text(
    data = modal_comparison_raw_df |>
      filter(source == "Simulated") |>
      mutate(label = sprintf("%.1f%%", pct)),
    aes(label = label),
    position = position_stack(vjust = 0.5),
    size = 3.6,
    colour = "black"
  ) +
  facet_wrap(~source, ncol = 2) +
  scale_fill_manual(
    values = c(
      "Walk" = "#D96584",
      "Bicycle" = "#C59600",
      "Public transport" = "#4FAA10",
      "Car" = "#19ADA2"
    ),
    name = "Mode"
  ) +
  scale_y_continuous(labels = label_percent(scale = 1),
                     expand = expansion(mult = c(0, 0.02))) +
  theme_lucid() +
  theme(axis.title = element_text(size = 13),
        axis.text = element_text(size = 12),
        strip.text = element_text(size = 12),
        legend.position = "right") +
  labs(x = NULL, y = "Mode share (%)",
       title = "Raw modal split comparison")

ggsave("plots/nine_euro/01c_modal_split_comparison_raw.pdf", p_modal_comparison_raw, width = 12, height = 6, device = cairo_pdf)
ggsave("plots/nine_euro/01c_modal_split_comparison_raw.png", p_modal_comparison_raw, width = 12, height = 6)

# ── 4. PT vs non-PT split per day ─────────────────────────────────────────────


OBSERVED_RAW_WAVE_N <- 3528

raw_shift_observed_counts <- modal_comparison_raw_observed |>
  transmute(
    source = "Observed",
    period,
    comparison_mode,
    pct,
    count = round(OBSERVED_RAW_WAVE_N * pct / 100)
  )

raw_shift_sim_counts <- reasoning |>
  filter(!is.na(mode_group)) |>
  mutate(
    comparison_mode = case_when(
      mode_group == "Walk" ~ "Walk",
      mode_group == "Bicycle" ~ "Bicycle",
      mode_group == "Car" ~ "Car",
      str_starts(mode_group, "PT ") ~ "Public transport",
      TRUE ~ NA_character_
    )
  ) |>
  filter(!is.na(comparison_mode)) |>
  count(day, comparison_mode, name = "count") |>
  mutate(period = if_else(day == 1, "Before", "During")) |>
  group_by(period, comparison_mode) |>
  summarise(
    count = if (first(period) == "Before") first(count) else round(mean(count)),
    .groups = "drop"
  ) |>
  group_by(period) |>
  mutate(pct = count / sum(count) * 100) |>
  ungroup() |>
  mutate(source = "Simulated")

raw_shift_counts <- bind_rows(raw_shift_observed_counts, raw_shift_sim_counts) |>
  mutate(
    source = factor(source, levels = c("Observed", "Simulated")),
    period = factor(period, levels = c("Before", "During")),
    comparison_mode = factor(comparison_mode,
                             levels = c("Walk", "Bicycle", "Public transport", "Car"))
  )

cat("\n-- Approximate raw modal-shift comparison: observed vs simulated --\n")
cat("Observed raw shares converted to approximate counts using n = 3528 per wave.\n")
print(raw_shift_counts |> arrange(source, period, comparison_mode))

raw_shift_glm_main <- glm(
  count ~ source + period + comparison_mode +
    source:period + source:comparison_mode + period:comparison_mode,
  family = poisson,
  data = raw_shift_counts
)

raw_shift_glm_interaction <- glm(
  count ~ source * period * comparison_mode,
  family = poisson,
  data = raw_shift_counts
)

raw_shift_lrt <- anova(raw_shift_glm_main, raw_shift_glm_interaction, test = "Chisq")

cat("\n-- Log-linear test: does modal change differ between observed and simulated? --\n")
print(raw_shift_lrt)

raw_shift_mode_changes <- raw_shift_counts |>
  select(source, period, comparison_mode, pct) |>
  pivot_wider(names_from = period, values_from = pct) |>
  mutate(change_pp = During - Before) |>
  arrange(source, comparison_mode)

cat("\n-- Before-to-during change in raw modal shares (percentage points) --\n")
print(raw_shift_mode_changes)
pt_by_day <- reasoning |>
  filter(!is.na(mode_group)) |>
  count(day, is_pt) |>
  group_by(day) |>
  mutate(pct = n / sum(n) * 100) |>
  ungroup() |>
  mutate(day = factor(day),
         category = if_else(is_pt, "Public transport", "Other modes"))

p_pt_day <- pt_by_day |>
  ggplot(aes(x = day, y = pct, fill = category, colour = category, group = category)) +
  stat_summary(fun = mean, geom = "point", size = 4, alpha = 0.9) +
  stat_summary(fun = mean, geom = "line",  linewidth = 2, alpha = 0.8) +
  geom_col(data = filter(pt_by_day, category == "Public transport"),
           aes(x = day, y = pct), fill = q3[1], alpha = 0.25, inherit.aes = FALSE) +
  scale_color_manual(values = c("Public transport" = q3[1], "Other modes" = q3[2]),
                     name = NULL) +
  scale_fill_manual(values  = c("Public transport" = q3[1], "Other modes" = q3[2]),
                    guide = "none") +
  scale_x_discrete(labels = DAY_LABELS) +
  scale_y_continuous(labels = label_percent(scale = 1),
                     expand = expansion(mult = c(0, 0.05))) +
  theme_lucid() +
  theme(legend.position = "inside",
        legend.position.inside = c(0.15, 0.9),
        axis.title = element_text(size = 13),
        axis.text  = element_text(size = 12)) +
  labs(x = NULL, y = "Share of legs (%)",
       title = "Public transport usage across simulation days")

ggsave("plots/nine_euro/02_pt_share_by_day.pdf", p_pt_day, width = 8, height = 6, device = cairo_pdf)
ggsave("plots/nine_euro/02_pt_share_by_day.png", p_pt_day, width = 8, height = 6)

# ── 5. Pass ownership and purchases from day_metrics ─────────────────────────

load_day_metrics <- function(day) {
  path <- file.path("9 Euro", paste0("day", day), "day_metrics.csv")
  df   <- read_csv(path, show_col_types = FALSE)
  df$day <- day
  df
}

day_metrics <- bind_rows(lapply(1:3, load_day_metrics))

# Normalise pass names
day_metrics <- day_metrics |>
  mutate(
    pass_type = case_when(
      str_detect(metric_name, "9_euro") ~ "9-Euro monthly pass",
      str_detect(metric_name, "monthly") ~ "Standard monthly pass",
      TRUE ~ metric_name
    )
  )

ownership_df <- day_metrics |>
  filter(metric_type == "mot_existing_owners") |>
  mutate(day = factor(day))

purchase_df <- day_metrics |>
  filter(metric_type == "mot_purchase") |>
  mutate(day = factor(day))

ownership_target_df <- day_metrics |>
  filter(
    (day == 1 & metric_name == "PT_monthly_pass") |
    (day %in% c(2, 3) & metric_name == "9_euro_PT_monthly_pass")
  ) |>
  filter(metric_type %in% c("mot_existing_owners", "mot_purchase")) |>
  mutate(category = recode(metric_type,
                           mot_existing_owners = "Existing owners",
                           mot_purchase = "New purchases")) |>
  group_by(day, category) |>
  summarise(n = sum(as.integer(value), na.rm = TRUE), .groups = "drop") |>
  mutate(day = factor(day))

ownership_share_df <- ownership_target_df |>
  left_join(
    bind_rows(lapply(1:3, function(d) {
      path <- file.path("9 Euro", paste0("day", d), "run_metrics.csv")
      df   <- read_csv(path, show_col_types = FALSE)
      tibble(day = factor(d), total_agents = as.integer(df$total_agents[1]))
    })),
    by = "day"
  ) |>
  group_by(day, total_agents) |>
  summarise(
    existing_owners = sum(n[category == "Existing owners"], na.rm = TRUE),
    new_purchases = sum(n[category == "New purchases"], na.rm = TRUE),
    .groups = "drop"
  ) |>
  mutate(non_pass_owners = total_agents - existing_owners - new_purchases) |>
  pivot_longer(cols = c(existing_owners, new_purchases, non_pass_owners),
               names_to = "category", values_to = "n") |>
  mutate(
    pct = n / total_agents * 100,
    category = recode(category,
                      existing_owners = "Existing owners",
                      new_purchases = "New purchases",
                      non_pass_owners = "Non-pass owners"),
    category = factor(category, levels = c("Existing owners", "New purchases", "Non-pass owners")),
    label = case_when(
      as.character(day) == "1" & category == "New purchases" ~ "",
      TRUE ~ sprintf("%.1f%%", pct)
    )
  )

p_ownership <- ownership_share_df |>
  ggplot(aes(x = day, y = pct, fill = category)) +
  geom_col(width = 0.7, position = "stack") +
  geom_text(aes(label = label),
            position = position_stack(vjust = 0.5),
            size = 4, colour = "black") +
  scale_fill_manual(values = c("Existing owners" = "#4FAA10",
                               "New purchases" = "#1F95D0",
                               "Non-pass owners" = "#D96584"),
                    name = NULL) +
  scale_x_discrete(labels = DAY_LABELS) +
  scale_y_continuous(labels = label_percent(scale = 1), expand = expansion(mult = c(0, 0.02))) +
  theme_lucid() +
  theme(legend.position = "right",
        axis.title = element_text(size = 13),
        axis.text  = element_text(size = 12)) +
  labs(x = NULL, y = "Share of agents (%)",
       title = "Pass ownership share across simulation days")

ggsave("plots/nine_euro/03_pass_ownership.pdf", p_ownership, width = 9, height = 6, device = cairo_pdf)
ggsave("plots/nine_euro/03_pass_ownership.png", p_ownership, width = 9, height = 6)

p_purchases <- purchase_df |>
  ggplot(aes(x = day, y = as.integer(value), fill = pass_type)) +
  geom_col(position = position_dodge(0.6), width = 0.5) +
  geom_text(aes(label = scales::comma(as.integer(value))),
            position = position_dodge(0.6), vjust = -0.5, size = 4) +
  scale_fill_manual(values = c("Standard monthly pass" = q3[1],
                               "9-Euro monthly pass"   = q3[2]),
                    name = NULL) +
  scale_x_discrete(labels = DAY_LABELS) +
  scale_y_continuous(labels = label_comma(), expand = expansion(mult = c(0, 0.15))) +
  theme_lucid() +
  theme(legend.position = "inside",
        legend.position.inside = c(0.7, 0.85),
        axis.title = element_text(size = 13),
        axis.text  = element_text(size = 12)) +
  labs(x = NULL, y = "New pass purchases",
       title = "Daily pass purchases (9-Euro ticket scenario)")

ggsave("plots/nine_euro/04_pass_purchases.pdf", p_purchases, width = 9, height = 6, device = cairo_pdf)
ggsave("plots/nine_euro/04_pass_purchases.png", p_purchases, width = 9, height = 6)

# ── 6. Modal split by economic status (if persona data available) ─────────────

if (!is.null(personas) && "income_bucket" %in% names(reasoning)) {

  modal_econ_sim <- reasoning |>
    filter(day != 1, !is.na(mode_group), !is.na(income_bucket), !is.na(distance_km)) |>
    mutate(mode_group = if_else(str_starts(mode_group, "PT "), "PT", mode_group)) |>
    filter(mode_group %in% c("Car", "Bicycle", "PT")) |>
    group_by(income_bucket, mode_group) |>
    summarise(distance_km = sum(as.numeric(distance_km), na.rm = TRUE), .groups = "drop") |>
    group_by(income_bucket) |>
    mutate(pct = distance_km / sum(distance_km) * 100) |>
    ungroup() |>
    mutate(
      income_bucket = factor(income_bucket, levels = INCOME_BUCKET_LEVELS),
      source = "simulated"
    )

  modal_econ_observed <- tribble(
    ~income_bucket, ~mode_group, ~pct, ~source,
    "0-<1500",    "PT",      68, "observed",
    "0-<1500",    "Car",     25, "observed",
    "0-<1500",    "Bicycle",  7, "observed",
    "1500-<4000", "PT",      54, "observed",
    "1500-<4000", "Car",     38, "observed",
    "1500-<4000", "Bicycle",  8, "observed",
    ">=4000",     "PT",      46, "observed",
    ">=4000",     "Car",     45, "observed",
    ">=4000",     "Bicycle",  9, "observed"
  ) |>
    mutate(income_bucket = factor(income_bucket, levels = INCOME_BUCKET_LEVELS))

  modal_econ <- bind_rows(modal_econ_observed, modal_econ_sim) |>
    mutate(source = factor(source, levels = c("observed", "simulated")))

  p_modal_econ <- modal_econ |>
    ggplot(aes(x = income_bucket, y = pct, fill = mode_group)) +
    geom_col(width = 0.75, position = "stack") +
    facet_wrap(~source, ncol = 2) +
    scale_fill_manual(values = c("Car" = q3[1], "Bicycle" = q3[2], "PT" = q3[3]), name = "Mode") +
    scale_y_continuous(labels = label_percent(scale = 1),
                       expand = expansion(mult = c(0, 0.02))) +
    theme_lucid() +
    theme(axis.text.x  = element_text(angle = 20, hjust = 1, size = 10),
          axis.title   = element_text(size = 13),
          strip.text   = element_text(size = 12),
          legend.position = "right") +
    labs(x = "Monthly net household income", y = "Share of distance travelled (%)",
         title = "Observed vs simulated modal split by household income bucket during the 9-Euro period")

  ggsave("plots/nine_euro/05_modal_by_income_bucket.pdf", p_modal_econ, width = 14, height = 6, device = cairo_pdf)
  ggsave("plots/nine_euro/05_modal_by_income_bucket.png", p_modal_econ, width = 14, height = 6)

  # PT share change: economic status × day
  pt_econ_day <- reasoning |>
    filter(!is.na(mode_group), !is.na(income_bucket)) |>
    group_by(day, income_bucket) |>
    summarise(pt_share = mean(is_pt) * 100, .groups = "drop") |>
    mutate(day = factor(day),
           income_bucket = factor(income_bucket, levels = INCOME_BUCKET_LEVELS))

  econ_levels <- INCOME_BUCKET_LEVELS

  p_pt_econ <- pt_econ_day |>
    ggplot(aes(x = day, y = pt_share, colour = income_bucket, group = income_bucket)) +
    geom_line(linewidth = 2, alpha = 0.85) +
    geom_point(size = 4.5, alpha = 0.9) +
    scale_color_manual(values = setNames(c(COL_PT, COL_CAR, COL_WALK), econ_levels),
                       name = "Monthly net household income") +
    scale_x_discrete(labels = DAY_LABELS) +
    scale_y_continuous(labels = label_percent(scale = 1),
                       expand = expansion(mult = c(0.05, 0.12))) +
    theme_lucid() +
    theme(legend.position = "right",
          axis.title = element_text(size = 13),
          axis.text  = element_text(size = 12)) +
    labs(x = NULL, y = "PT share (%)",
         title = "Public transport share by household income bucket across days")

  ggsave("plots/nine_euro/06_pt_share_by_income_bucket.pdf", p_pt_econ, width = 10, height = 6, device = cairo_pdf)
  ggsave("plots/nine_euro/06_pt_share_by_income_bucket.png", p_pt_econ, width = 10, height = 6)

} else {
  message("Skipping economic-status plots — persona data not available.")
}

# ── 7. Modal split by occupation (if available) ───────────────────────────────

if (!is.null(personas) && "occupation" %in% names(reasoning)) {

  pt_occ_day <- reasoning |>
    filter(!is.na(mode_group), !is.na(occupation)) |>
    group_by(day, occupation) |>
    summarise(pt_share = mean(is_pt) * 100, .groups = "drop") |>
    mutate(day = factor(day))

  occ_levels <- pt_occ_day |>
    group_by(occupation) |>
    summarise(mean_pt = mean(pt_share)) |>
    arrange(mean_pt) |>
    pull(occupation)

  p_pt_occ <- pt_occ_day |>
    mutate(occupation = factor(occupation, levels = occ_levels)) |>
    ggplot(aes(x = day, y = pt_share, colour = occupation, group = occupation)) +
    geom_line(linewidth = 1.8, alpha = 0.8) +
    geom_point(size = 4, alpha = 0.9) +
    scale_color_manual(values = setNames(rep(q6, length.out = length(occ_levels)), occ_levels),
                       name = "Occupation") +
    scale_x_discrete(labels = DAY_LABELS) +
    scale_y_continuous(labels = label_percent(scale = 1),
                       expand = expansion(mult = c(0.05, 0.1))) +
    theme_lucid() +
    theme(legend.position = "right",
          axis.title = element_text(size = 13),
          axis.text  = element_text(size = 12)) +
    labs(x = NULL, y = "PT share (%)",
         title = "Public transport share by occupation across days")

  ggsave("plots/nine_euro/07_pt_share_by_occupation.pdf", p_pt_occ, width = 11, height = 6, device = cairo_pdf)
  ggsave("plots/nine_euro/07_pt_share_by_occupation.png", p_pt_occ, width = 11, height = 6)

} else {
  message("Skipping occupation plots — persona data not available.")
}

# ── 8. Inconsistency analysis ─────────────────────────────────────────────────

load_inconsistencies <- function(day) {
  path <- file.path("9 Euro", paste0("day", day), "inconsistencies.csv")
  df   <- read_csv(path, show_col_types = FALSE)
  df$day <- day
  df
}

scenario_inc <- bind_rows(lapply(1:3, load_inconsistencies))

scenario_inc <- scenario_inc |>
  mutate(
    day = factor(day),
    rule_label = case_when(
      trigger_rule == "ownership"               ~ "Ownership",
      trigger_rule == "route_availability"      ~ "Route availability",
      trigger_rule %in% c("first_use_location",
                          "activation")         ~ "First-use location",
      trigger_rule == "home_consistency_return" ~ "Home return",
      TRUE ~ str_to_title(str_replace_all(trigger_rule, "_", " "))
    )
  )

inc_day_counts <- scenario_inc |>
  count(day, rule_label) |>
  group_by(day) |>
  mutate(pct = n / sum(n) * 100) |>
  ungroup()

rule_levs  <- inc_day_counts |> count(rule_label, wt = n, sort = TRUE) |> pull(rule_label)
rule_cols <- c(
  "Route availability" = COL_WALK,
  "First-use location" = COL_BICYCLE,
  "Home return" = COL_PT,
  "Ownership" = COL_CAR,
  "Stickiness" = COL_PT_DAY
)[rule_levs]

p_inc_days <- inc_day_counts |>
  mutate(rule_label = factor(rule_label, levels = rule_levs)) |>
  ggplot(aes(x = day, y = pct, fill = rule_label)) +
  geom_col(width = 0.65) +
  scale_fill_manual(values = rule_cols, name = "Rule violated") +
  scale_x_discrete(labels = DAY_LABELS) +
  scale_y_continuous(labels = label_percent(scale = 1),
                     expand = expansion(mult = c(0, 0.02))) +
  theme_lucid() +
  theme(legend.position = "right",
        axis.title = element_text(size = 13),
        axis.text  = element_text(size = 12)) +
  labs(x = NULL, y = "Share of inconsistencies (%)",
       title = "Inconsistency types across simulation days (9-Euro scenario)")

ggsave("plots/nine_euro/08_inconsistency_types_by_day.pdf", p_inc_days, width = 10, height = 6, device = cairo_pdf)
ggsave("plots/nine_euro/08_inconsistency_types_by_day.png", p_inc_days, width = 10, height = 6)

# ── 9. Run-level summary table ────────────────────────────────────────────────

run_sum <- bind_rows(lapply(1:3, function(d) {
  path <- file.path("9 Euro", paste0("day", d), "run_metrics.csv")
  df   <- read_csv(path, show_col_types = FALSE)
  df$day <- d
  df
})) |>
  select(day, everything())

print(run_sum)
writexl::write_xlsx(run_sum, "plots/nine_euro/run_metrics_summary.xlsx")
classify_frequency_change <- function(during_avg, before) {
  case_when(
    during_avg < before ~ "Less",
    during_avg > before ~ "More",
    TRUE                ~ "Unchanged"
  )
}

agent_mode_change <- reasoning |>
  filter(!is.na(mode_group)) |>
  group_by(agent_id, day) |>
  summarise(
    car_trips = sum(mode_group == "Car", na.rm = TRUE),
    pt_trips  = sum(is_pt, na.rm = TRUE),
    .groups = "drop"
  ) |>
  complete(agent_id, day = 1:3, fill = list(car_trips = 0, pt_trips = 0)) |>
  group_by(agent_id) |>
  summarise(
    car_before = car_trips[day == 1][1],
    car_during_avg = ceiling(mean(car_trips[day %in% c(2, 3)])),
    pt_before = pt_trips[day == 1][1],
    pt_during_avg = ceiling(mean(pt_trips[day %in% c(2, 3)])),
    .groups = "drop"
  ) |>
  mutate(
    car_change = classify_frequency_change(car_during_avg, car_before),
    pt_change  = classify_frequency_change(pt_during_avg, pt_before)
  )

change_levels <- c("Less", "Unchanged", "More")

mode_change_crosstab <- agent_mode_change |>
  mutate(
    car_change = factor(car_change, levels = change_levels),
    pt_change  = factor(pt_change, levels = change_levels)
  ) |>
  count(car_change, pt_change) |>
  complete(car_change, pt_change, fill = list(n = 0)) |>
  mutate(pct = n / sum(n) * 100) |>
  arrange(car_change, pt_change) |>
  select(car_change, pt_change, pct) |>
  pivot_wider(names_from = pt_change, values_from = pct)

cat("\nCross-tabulation of changes in car and public transport use frequency (%)\n")
print(mode_change_crosstab)

# ════════════════════════════════════════════════════════════════════════════
# STATISTICAL TESTS
# ════════════════════════════════════════════════════════════════════════════

# ── S1. Chi-square: overall modal split differs across days? ──────────────────
# Build count matrix: rows = mode, cols = day.

modal_ct <- reasoning |>
  filter(!is.na(mode_group)) |>
  count(day, mode_group) |>
  pivot_wider(names_from = day, values_from = n, values_fill = 0) |>
  column_to_rownames("mode_group") |>
  as.matrix()

cramers_v <- function(x) {
  ct <- suppressWarnings(chisq.test(x))
  unname(sqrt(ct$statistic / (sum(x) * (min(dim(x)) - 1))))
}

chisq_modal <- chisq.test(modal_ct)
cat("\n── Chi-square: modal split differs across days ──\n")
print(chisq_modal)
cat(sprintf("Cramér's V = %.3f\n", cramers_v(modal_ct)))

# ── S2. Pairwise day comparisons (modal counts, Holm-corrected) ───────────────

day_pairs <- combn(as.character(sort(unique(reasoning$day))), 2, simplify = FALSE)

pairwise_modal <- lapply(day_pairs, function(dp) {
  sub <- modal_ct[, dp, drop = FALSE]
  ct  <- chisq.test(sub)
  tibble(day_a = dp[1], day_b = dp[2],
         chi_sq = ct$statistic, df = ct$parameter,
         p_raw = ct$p.value, cramers_v = cramers_v(sub))
}) |>
  bind_rows() |>
  mutate(p_holm = p.adjust(p_raw, method = "holm"))

cat("\n── Pairwise day comparisons (modal split, Holm-corrected) ──\n")
print(pairwise_modal)

# ── S3. Mixed logistic: PT choice ~ day, random intercept per agent ───────────
# day 1 = reference (full-price weekday)

reasoning_model <- reasoning |>
  filter(!is.na(mode_group)) |>
  mutate(
    day_f     = factor(day, levels = c(1, 2, 3),
                       labels = c("Day1_full_price", "Day2_9euro_sat", "Day3_9euro_weekday")),
    day_f     = relevel(day_f, ref = "Day1_full_price"),
    income_bucket = factor(income_bucket, levels = INCOME_BUCKET_LEVELS),
    agent_id  = factor(agent_id)
  )

cat("\n── Mixed logistic: is_pt ~ day + (1|agent_id) ──\n")
glmer_pt <- glmer(is_pt ~ day_f + (1 | agent_id),
                  data   = reasoning_model,
                  family = binomial,
                  control = glmerControl(optimizer = "bobyqa"))

cat("\nMixed logistic report:\n")
print(report(glmer_pt))
cat("\nMixed logistic parameters:\n")
print(report_table(glmer_pt))

if (lme4::isSingular(glmer_pt))
  message("⚠ glmer_pt: singular fit — check random-effects structure")

cat("\nOdds ratios (PT choice vs Day 1):\n")
or_pt <- data.frame(
  OR      = exp(fixef(glmer_pt)),
  CI_low  = exp(confint(glmer_pt, parm = "beta_", method = "Wald")[, 1]),
  CI_high = exp(confint(glmer_pt, parm = "beta_", method = "Wald")[, 2])
) |>
  rownames_to_column("term") |>
  mutate(across(c(OR, CI_low, CI_high), ~round(.x, 3)))
print(or_pt)

# ── S4. McNemar: paired PT use per agent, Day 1 vs Day 2 / Day 1 vs Day 3 ────
# Collapse to one row per agent per day: did the agent use PT at all?

agent_pt_day <- reasoning |>
  filter(!is.na(mode_group)) |>
  group_by(agent_id, day) |>
  summarise(used_pt = any(is_pt), .groups = "drop")

run_mcnemar <- function(day_a, day_b) {
  wide <- agent_pt_day |>
    filter(day %in% c(day_a, day_b)) |>
    pivot_wider(names_from = day, values_from = used_pt, names_prefix = "day_") |>
    filter(!is.na(.data[[paste0("day_", day_a)]]),
           !is.na(.data[[paste0("day_", day_b)]]))
  ct <- table(wide[[paste0("day_", day_a)]], wide[[paste0("day_", day_b)]])
  mc <- mcnemar.test(ct)
  cat(sprintf("\nMcNemar Day %d vs Day %d: X²(1) = %.2f, p = %.4f (n = %d agents)\n",
              day_a, day_b, mc$statistic, mc$p.value, nrow(wide)))
  tibble(comparison = paste("Day", day_a, "vs Day", day_b),
         chi_sq = mc$statistic, p_value = mc$p.value, n_agents = nrow(wide))
}

cat("\n── McNemar tests: paired PT use per agent ──\n")
mcnemar_results <- bind_rows(
  run_mcnemar(1, 2),
  run_mcnemar(1, 3),
  run_mcnemar(2, 3)
)

# ── S5. Economic status × day interaction (if persona data available) ──────────

econ_stats <- NULL
if (!is.null(personas) && "income_bucket" %in% names(reasoning_model)) {
  cat("\n── Mixed logistic: is_pt ~ day_f * income_bucket + (1|agent_id) ──\n")
  glmer_econ <- glmer(
    is_pt ~ day_f * income_bucket + (1 | agent_id),
    data   = reasoning_model |> filter(!is.na(income_bucket)),
    family = binomial,
    control = glmerControl(optimizer = "bobyqa")
  )
  cat("\nIncome bucket interaction model report:\n")
  print(report(glmer_econ))
  cat("\nIncome bucket interaction model parameters:\n")
  print(report_table(glmer_econ))

  if (lme4::isSingular(glmer_econ))
    message("⚠ glmer_econ: singular fit — check random-effects structure")

  econ_stats <- as.data.frame(coef(summary(glmer_econ))) |>
    rownames_to_column("term") |>
    mutate(OR = exp(Estimate), p_holm = p.adjust(`Pr(>|z|)`, method = "holm"))
}

# ════════════════════════════════════════════════════════════════════════════
# REAL-WORLD COMPARISON TEMPLATE
# ════════════════════════════════════════════════════════════════════════════
# Fill in empirical modal split proportions from the MiD / KiD / BMVI reports
# covering the same Berlin area and time period.
# Sources to check:
#   - MiD 2017 (Mobilität in Deutschland), Berlin subsample
#   - 9-Euro ticket evaluation reports (BMVI, Oct 2022)
#   - Destatis / VDV ridership data June–Aug 2022
#
# Proportions should sum to 1 within each period.
# Set to NA for modes not reported in the empirical source.

empirical_modal <- tribble(
  ~mode_group,         ~pre_9euro_real, ~during_9euro_real,
  # ── INSERT VALUES BELOW ───────────────────────────────────────────────────
  "Walk",              0.1159,          0.1178,
  "Bicycle",           0.1591,          0.1725,
  "Car",               0.6069,          0.5883,
  "PT",                0.1181,          0.1214
  # ─────────────────────────────────────────────────────────────────────────
)

# Simulated proportions (Day 1 = pre, Day 2/3 average = during)
sim_modal <- reasoning |>
  filter(!is.na(mode_group)) |>
  mutate(mode_group = if_else(str_starts(mode_group, "PT "), "PT", mode_group)) |>
  mutate(period = if_else(day == 1, "pre_9euro_sim", "during_9euro_sim")) |>
  count(mode_group, period) |>
  group_by(period) |>
  mutate(prop = n / sum(n)) |>
  ungroup() |>
  select(mode_group, period, prop) |>
  pivot_wider(names_from = period, values_from = prop, values_fill = 0)

comparison_df <- empirical_modal |>
  left_join(sim_modal, by = "mode_group")

income_bucket_realworld <- tribble(
  ~income_bucket, ~mode_group, ~during_9euro_real,
  "0-<1500",     "PT",         0.68,
  "0-<1500",     "Car",        0.25,
  "0-<1500",     "Bicycle",    0.07,
  "1500-<4000",  "PT",         0.54,
  "1500-<4000",  "Car",        0.38,
  "1500-<4000",  "Bicycle",    0.08,
  ">=4000",      "PT",         0.46,
  ">=4000",      "Car",        0.45,
  ">=4000",      "Bicycle",    0.09
) |>
  mutate(income_bucket = factor(income_bucket, levels = INCOME_BUCKET_LEVELS))

income_bucket_sim <- reasoning |>
  filter(day != 1, !is.na(mode_group), !is.na(income_bucket), !is.na(distance_km)) |>
  mutate(
    income_bucket = factor(income_bucket, levels = INCOME_BUCKET_LEVELS),
    mode_group = if_else(str_starts(mode_group, "PT "), "PT", mode_group)
  ) |>
  filter(mode_group %in% c("Car", "Bicycle", "PT")) |>
  group_by(income_bucket, mode_group) |>
  summarise(distance_km = sum(as.numeric(distance_km), na.rm = TRUE), .groups = "drop_last") |>
  mutate(during_9euro_sim = distance_km / sum(distance_km)) |>
  ungroup() |>
  select(income_bucket, mode_group, during_9euro_sim)

income_bucket_comparison <- income_bucket_realworld |>
  left_join(income_bucket_sim, by = c("income_bucket", "mode_group"))

cat("\n── Simulated vs empirical modal split (fill in empirical columns) ──\n")
print(comparison_df)
cat("\nâ”€â”€ Income-bucket during-period modal split comparison â”€â”€\n")
print(income_bucket_comparison)

# Chi-square goodness-of-fit: sim vs real (runs once empirical data is filled)
run_gof_test <- function(sim_props, real_props, label) {
  valid <- !is.na(real_props) & !is.na(sim_props)
  if (sum(valid) < 2) {
    cat(sprintf("\n[%s] Not enough empirical data to run GOF test.\n", label))
    return(invisible(NULL))
  }
  # Rescale to sum to 1 over available modes
  real_p <- real_props[valid] / sum(real_props[valid])
  sim_n  <- round(sim_props[valid] * 10000)  # synthetic N
  ct     <- chisq.test(sim_n, p = real_p)
  cat(sprintf("\n── GOF chi-square [%s]: X²(%d) = %.2f, p = %.4f ──\n",
              label, ct$parameter, ct$statistic, ct$p.value))
  ct
}

run_gof_test(comparison_df$pre_9euro_sim,    comparison_df$pre_9euro_real,    "Pre  – sim vs real")
run_gof_test(comparison_df$during_9euro_sim, comparison_df$during_9euro_real, "During – sim vs real")

# PT share change: simulated vs empirical (two-proportion z-test)
# Fill in these two values once empirical data is available:
pt_real_pre    <- 0.1181
pt_real_during <- 0.1214

pt_sim_pre    <- sim_modal |>
  filter(mode_group == "PT") |>
  summarise(p = sum(pre_9euro_sim, na.rm = TRUE)) |> pull(p)

pt_sim_during <- sim_modal |>
  filter(mode_group == "PT") |>
  summarise(p = sum(during_9euro_sim, na.rm = TRUE)) |> pull(p)

cat(sprintf("\nSimulated PT share — pre: %.1f%%, during: %.1f%%\n",
            pt_sim_pre * 100, pt_sim_during * 100))
cat(sprintf("Empirical PT share — pre: %s, during: %s\n",
            if (is.na(pt_real_pre)) "INSERT" else sprintf("%.1f%%", pt_real_pre * 100),
            if (is.na(pt_real_during)) "INSERT" else sprintf("%.1f%%", pt_real_during * 100)))

if (!is.na(pt_real_pre) && !is.na(pt_real_during)) {
  n_legs_pre    <- reasoning |> filter(day == 1) |> nrow()
  n_legs_during <- reasoning |> filter(day != 1) |> nrow()

  cat("\n── Two-proportion z-test: sim PT change vs empirical PT change ──\n")
  # Compare observed sim change against the empirically expected post-proportion
  z_test <- prop.test(
    x = c(round(pt_sim_during * n_legs_during), round(pt_real_during * n_legs_during)),
    n = c(n_legs_during, n_legs_during)
  )
  print(z_test)
}

# ── Export ────────────────────────────────────────────────────────────────────

write_xlsx(
  list(
    chisq_overall        = tibble(chi_sq  = chisq_modal$statistic,
                                  df      = chisq_modal$parameter,
                                  p_value = chisq_modal$p.value),
    pairwise_days_modal  = pairwise_modal,
    mcnemar_pt           = mcnemar_results,
    logistic_OR_day      = or_pt,
    econ_interaction     = if (!is.null(econ_stats)) econ_stats else tibble(note = "No persona data"),
    realworld_comparison = comparison_df,
    income_bucket_modal_comparison = income_bucket_comparison,
    raw_shift_counts = raw_shift_counts |> arrange(source, period, comparison_mode),
    raw_shift_mode_changes = raw_shift_mode_changes,
    raw_shift_loglinear_lrt = raw_shift_lrt |> as.data.frame() |> tibble::rownames_to_column("model")
  ),
  "plots/nine_euro/nine_euro_stats.xlsx"
)

# ── HTML diagnostic dashboards (run last so console output doesn't interleave) ─

cat("\nGenerating model dashboards (HTML)…\n")
invisible(capture.output(suppressMessages({
  try(easystats::model_dashboard(glmer_pt,
        output_file = "plots/nine_euro/glmer_pt_dashboard.html"), silent = TRUE)
  if (exists("glmer_econ")) {
    try(easystats::model_dashboard(glmer_econ,
          output_file = "plots/nine_euro/glmer_econ_dashboard.html"), silent = TRUE)
  }
})))

cat("\n9-Euro analysis complete. Plots written to plots/nine_euro/\n")



