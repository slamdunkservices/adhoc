library(tidyverse)

# -----------------------------
# Helpers
# -----------------------------

read_bets <- function(path) {
  readr::read_csv(path, show_col_types = FALSE) %>%
    transmute(
      date    = as.Date(date),
      game_id,
      units_net = units_standardized_net
    )
}

plot_shuffle_variance <- function(data, title, n_sims = 250, seed = 42,
                                  overlay_data = NULL, overlay_title = NULL) {
  data <- data %>% arrange(date, game_id)

  game_blocks <- data %>%
    group_by(game_id) %>%
    summarise(units = list(units_net), .groups = "drop")

  true_df <- tibble(
    bet_number  = seq_len(nrow(data)),
    running_sum = cumsum(data$units_net)
  )

  set.seed(seed)
  sim_df <- map_dfr(seq_len(n_sims), function(i) {
    shuffled <- game_blocks[sample(nrow(game_blocks)), ]
    u <- unlist(shuffled$units)
    tibble(
      bet_number  = seq_along(u),
      running_sum = cumsum(u),
      run         = i
    )
  })

  shuffled_label <- paste0(title, " shuffled")
  actual_label   <- paste0(title, " actuals")
  sim_df <- sim_df %>% mutate(label = shuffled_label)

  color_map <- c(
    setNames("grey70",  shuffled_label),
    setNames("#E69F00", actual_label)
  )
  if (!is.null(overlay_data) && !is.null(overlay_title)) {
    overlay_actual_label <- paste0(overlay_title, " actuals")
    color_map <- c(color_map, setNames("#0072B2", overlay_actual_label))
  }

  p <- ggplot() +
    geom_line(
      data = sim_df,
      aes(x = bet_number, y = running_sum, group = run, color = label),
      alpha = 0.2, linewidth = 0.4
    ) +
    geom_hline(yintercept = 0, linetype = "dashed", color = "grey70") +
    geom_line(
      data = true_df,
      aes(x = bet_number, y = running_sum, color = actual_label),
      linewidth = 1.4
    ) +
    scale_color_manual(values = color_map, name = NULL) +
    guides(color = guide_legend(override.aes = list(alpha = 1, linewidth = 1.2))) +
    labs(
      title = "Cumulative Unit Performance — Actuals vs. Simulated",
      x     = "Bet Number",
      y     = "Cumulative Units"
    ) +
    theme_minimal(base_size = 14) +
    theme(
      plot.title      = element_text(hjust = 0.5, face = "bold"),
      legend.position = "bottom"
    )

  if (!is.null(overlay_data)) {
    overlay_df <- overlay_data %>%
      arrange(date, game_id) %>%
      mutate(
        bet_number  = row_number(),
        running_sum = cumsum(units_net)
      )
    p <- p + geom_line(
      data = overlay_df,
      aes(x = bet_number, y = running_sum, color = overlay_actual_label),
      linewidth = 1.4
    )
  }

  p
}

# -----------------------------
# Config
# -----------------------------

bet_files <- tribble(
  ~name,        ~title,          ~path,
  "nba_23_24",  "NBA 2023-2024", "NBA/2023-2024/bet_tracking_consolidated.csv",
  "nba_24_25",  "NBA 2024-2025", "NBA/2024-2025/bet_tracking_consolidated.csv",
  "nba_25_26",  "NBA 2025-2026", "NBA/2025-2026/bet_tracking_consolidated.csv",
  "wnba_24",    "WNBA 2024",     "WNBA/2024/bet_tracking_consolidated.csv",
  "wnba_25",    "WNBA 2025",     "WNBA/2025/bet_tracking_consolidated.csv",
  "wnba_26",    "WNBA 2026",     "WNBA/2026/bet_tracking_consolidated.csv"
)

# -----------------------------
# Run
# Change `shuffle_target` to any name in bet_files above.
# Optionally set `overlay_target` to a second season to draw its actuals in green.
# Set `overlay_target <- NULL` to omit the overlay.
# -----------------------------

shuffle_target  <- "wnba_25"
overlay_target  <- "wnba_26"   # or NULL

target <- bet_files %>% filter(name == shuffle_target)
data   <- read_bets(target$path)

if (!is.null(overlay_target)) {
  overlay_row  <- bet_files %>% filter(name == overlay_target)
  overlay_data <- read_bets(overlay_row$path)
  overlay_title <- overlay_row$title
} else {
  overlay_data  <- NULL
  overlay_title <- NULL
}

plot_shuffle_variance(
  data, title = target$title, n_sims = 250,
  overlay_data = overlay_data, overlay_title = overlay_title
)
