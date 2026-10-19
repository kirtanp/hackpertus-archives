import { glob } from "astro/loaders";
import { defineCollection } from "astro:content";
import { docsSchema } from "@astrojs/starlight/schema";

export const collections = {
	docs: defineCollection({
		loader: glob({ pattern: "**/*.{md,mdx}", base: "./src/content/docs" }),
		schema: docsSchema(),
	}),
};
