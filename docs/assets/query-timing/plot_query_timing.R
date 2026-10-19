# The chart behind docs/src/content/docs/reference/query-timing.mdx: how long one question takes to answer.
#
# Reads only the committed measurements next to this file (measurements.csv, one row per question, written by
# measure_query_timing.py) and measurements.meta.json for the run's comparability facts. No network access.
#
#     Rscript docs/assets/query-timing/plot_query_timing.R
#
# Writes the same figure twice: the committed source copy next to the data, and the copy the documentation site
# serves from docs/public/ (Starlight serves page images from public/, the way docs/public/architecture/ works).

library(ggplot2)
library(jsonlite)

args <- commandArgs(trailingOnly = FALSE)
script <- sub("^--file=", "", args[grep("^--file=", args)])
here <- if (length(script) > 0) dirname(normalizePath(script)) else getwd()
out_asset <- file.path(here, "query-timing.png")
out_public <- file.path(dirname(dirname(here)), "public", "query-timing", "query-timing.png")

d <- read.csv(file.path(here, "measurements.csv"), stringsAsFactors = FALSE)
meta <- fromJSON(file.path(here, "measurements.meta.json"))
n <- nrow(d)
median_s <- median(d$seconds)

# One row per question, shortest first, with the whole-set summary box at the top; the question text is the label.
d$label <- ifelse(nchar(d$question) > 52, paste0(substr(d$question, 1, 49), "..."), d$question)
order_by_time <- d$label[order(d$seconds)]
d$label <- factor(d$label, levels = c(order_by_time, sprintf("All %d questions", n)))
seed <- if (!is.null(meta$seed)) meta$seed else 11

caption <- paste0(
  sprintf("Source: docs/assets/query-timing/measurements.csv - %d questions from archai.qa_eval.all_cases, seed %s,\n",
          n, seed),
  sprintf("one qa.answer() call each (dynamic rewrite). Model %s on a %s endpoint (%s, %s, %s cores).",
          meta$model, meta$endpoint$kind, meta$machine$machine, meta$machine$cpu, meta$machine$cpu_count)
)

p <- ggplot(d, aes(y = label, colour = dataset)) +
  geom_segment(aes(x = 0, xend = seconds, yend = label), linewidth = 0.4, colour = "grey75") +
  geom_point(aes(x = seconds), size = 2.4) +
  geom_vline(xintercept = median_s, linetype = "dashed", colour = "grey35") +
  annotate("text", x = median_s, y = length(levels(d$label)) + 0.35, hjust = -0.06, vjust = 0, size = 3.1,
           colour = "grey30", label = sprintf("median %.1f s", median_s)) +
  scale_colour_manual(values = c(stapfer = "#1b6ca8", consultation = "#c1440e"), name = NULL,
                      labels = c(stapfer = "1799 school survey (Stapfer)", consultation = "Federal consultations 1960-1991")) +
  scale_x_continuous(limits = c(0, NA), expand = expansion(mult = c(0, 0.06))) +
  labs(title = "How long the Q&A path takes per question",
       subtitle = sprintf("%d questions, one at a time, sequential. Median %.1f s, total %.0f s.", n, median_s, sum(d$seconds)),
       x = "Wall-clock time to answer one question (seconds)", y = NULL, caption = caption) +
  theme_minimal(base_size = 12) +
  theme(panel.grid.major.y = element_blank(),
        panel.grid.minor = element_blank(),
        plot.title = element_text(face = "bold"),
        plot.subtitle = element_text(colour = "grey25"),
        plot.caption = element_text(hjust = 0, colour = "grey35", size = 8.5, lineheight = 1.15),
        legend.position = "bottom",
        axis.text.y = element_text(size = 9),
        plot.margin = margin(12, 16, 10, 12))

# The whole-set box sits above the per-question rows so the strip and the spread read together.
p <- p + geom_boxplot(data = transform(d, label = factor(sprintf("All %d questions", n), levels = levels(d$label))),
                      aes(x = seconds, y = label), width = 0.5, colour = "grey25", fill = "grey92",
                      outlier.shape = NA) +
  coord_cartesian(ylim = c(0.4, length(levels(d$label)) + 0.9), clip = "off")

dir.create(dirname(out_public), showWarnings = FALSE, recursive = TRUE)
ggsave(out_asset, p, width = 11, height = 8.5, dpi = 150, bg = "white")
invisible(file.copy(out_asset, out_public, overwrite = TRUE))
cat(sprintf("wrote %s and %s\n", out_asset, out_public))
