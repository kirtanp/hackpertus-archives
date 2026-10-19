import { defineCollection } from "astro:content";
import { glob } from "astro/loaders";
import { z } from "astro/zod";

const slidesSchema = z.object({
	title: z.string(),
	description: z.string(),
	pubDate: z.coerce.date(),
	order: z.number().optional(),
	minutes: z.number().optional(),
	presenter: z.string().optional(),
	theme: z.enum(["black", "night", "moon", "white", "simple"]).default("black"),
	transition: z.string().default("slide"),
	controls: z.boolean().default(true),
	progress: z.boolean().default(true),
	/** per-deck font multiplier (CSS var --deck-font-scale), default 1 */
	fontScale: z.number().optional(),
});

// One deck per file, slides separated by `---` (Reveal's markdown separator).
const slides = defineCollection({
	loader: glob({ pattern: "**/*.{md,mdx}", base: "./src/content/slides" }),
	schema: slidesSchema,
});

export const collections = { slides };
