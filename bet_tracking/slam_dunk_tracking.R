library(tidyverse)
library(readxl)

# -----------------------------
# Helpers
# -----------------------------

read_old_bets <- function(path) {
  readxl::read_excel(path) %>%
    mutate(
      payout_mult = if_else(Odds > 0, Odds / 100, 100 / abs(Odds)),
      units_net = case_when(
        Outcome == "Win"  ~ Units * payout_mult,
        Outcome == "Loss" ~ -Units,
        TRUE ~ NA_real_
      )
    ) %>%
    transmute(
      date = as.Date(Date),
      units_net
    )
}

read_standard_bets <- function(path) {
  readr::read_csv(path, show_col_types = FALSE) %>%
    transmute(
      date = as.Date(date),
      units_net = units_standardized_net
    )
}

get_date_breaks <- function(dates) {
  date_span <- as.numeric(max(dates, na.rm = TRUE) - min(dates, na.rm = TRUE))
  
  case_when(
    date_span <= 90  ~ "2 weeks",
    date_span <= 180 ~ "1 month",
    date_span <= 365 ~ "2 months",
    date_span <= 730 ~ "3 months",
    TRUE             ~ "6 months"
  )
}

plot_running_sum <- function(data, title, x_axis = c("bet_number", "date")) {
  
  x_axis <- match.arg(x_axis)
  
  data <- data %>%
    mutate(date = as.Date(date)) %>%
    arrange(date)
  
  if (x_axis == "date") {
    
    plot_df <- data %>%
      group_by(date) %>%
      summarise(
        units_net = sum(units_net, na.rm = TRUE),
        .groups = "drop"
      ) %>%
      arrange(date) %>%
      mutate(
        running_sum = cumsum(units_net)
      )
    
  } else {
    
    plot_df <- data %>%
      mutate(
        bet_number = row_number(),
        running_sum = cumsum(units_net)
      )
  }
  
  p <- ggplot(plot_df, aes(x = .data[[x_axis]], y = running_sum)) +
    geom_line(linewidth = 1.2, alpha = 0.9) +
    labs(
      title = paste(title, "Performance"),
      x = if_else(x_axis == "bet_number", "Bet Number", "Date"),
      y = "Units"
    ) +
    theme_minimal(base_size = 14) +
    theme(
      plot.title = element_text(hjust = 0.5, face = "bold"),
      legend.position = "bottom"
    )
  
  if (x_axis == "date") {
    p <- p +
      scale_x_date(
        date_labels = "%b %Y",
        date_breaks = get_date_breaks(plot_df$date)
      ) +
      theme(
        axis.text.x = element_text(angle = 45, hjust = 1)
      )
  }
  
  p
}

# -----------------------------
# Config
# -----------------------------

bet_files <- tribble(
  ~name,        ~title,             ~path,                                             ~reader,
  "nba_21_22", "NBA 2021-2022",     "NBA/2021-2022/prop_bets_21_22.xlsx",              "old",
  "nba_22_23", "NBA 2022-2023",     "NBA/2022-2023/prop_bets_22_23.xlsx",              "old",
  "nba_23_24", "NBA 2023-2024",     "NBA/2023-2024/bet_tracking_consolidated.csv",     "standard",
  "nba_24_25", "NBA 2024-2025",     "NBA/2024-2025/bet_tracking_consolidated.csv",     "standard",
  "nba_25_26", "NBA 2025-2026",     "NBA/2025-2026/bet_tracking_consolidated.csv",     "standard",
  "wnba_24",   "WNBA 2024",         "WNBA/2024/bet_tracking_consolidated.csv",         "standard",
  "wnba_25",   "WNBA 2025",         "WNBA/2025/bet_tracking_consolidated.csv",         "standard"
)

# -----------------------------
# Read / clean data
# -----------------------------

bet_data <- bet_files %>%
  mutate(
    data = map2(
      path,
      reader,
      ~ if (.y == "old") read_old_bets(.x) else read_standard_bets(.x)
    )
  )

combined_data_frame <- bet_data %>%
  select(data) %>%
  unnest(data) %>%
  arrange(date)

# -----------------------------
# Plots
# -----------------------------

plots_by_bet_num <- bet_data %>%
  mutate(
    plot = map2(data, title, ~ plot_running_sum(.x, .y, x_axis = "bet_number"))
  ) %>%
  select(name, title, plot)

plots_by_date <- bet_data %>%
  mutate(
    plot = map2(data, title, ~ plot_running_sum(.x, .y, x_axis = "date"))
  ) %>%
  select(name, title, plot)

plot_combined_by_bet_num <- plot_running_sum(
  combined_data_frame,
  title = "All-Time",
  x_axis = "bet_number"
)

plot_combined_by_date <- plot_running_sum(
  combined_data_frame,
  title = "All-Time",
  x_axis = "date"
)

# -----------------------------
# Visualize
# -----------------------------

# Plots by Bet Number
walk(plots_by_bet_num$plot, print)
print(plot_combined_by_bet_num)

# Plots by Date
walk(plots_by_date$plot, print)
print(plot_combined_by_date)
