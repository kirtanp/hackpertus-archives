import type { CollectionEntry } from "astro:content";
import { getCollection } from "astro:content";

/** The decks in presentation order (frontmatter `order`, then filename). */
export async function sortedDecks(): Promise<CollectionEntry<"slides">[]> {
	return (await getCollection("slides")).sort(
		(a, b) => (a.data.order ?? 99) - (b.data.order ?? 99),
	);
}

/** The deck's route segment: `archai.md` -> `archai`. */
export function deckSlug(slide: CollectionEntry<"slides">) {
	return slide.id.replace(/\.(md|mdx)$/, "");
}

/**
 * The deck body is raw markdown injected into the Reveal textarea; strip the
 * frontmatter defensively the way the Astro 5 content loader returns it.
 */
export function deckBody(slide: CollectionEntry<"slides">) {
	return (slide.body ?? "").replace(/^---[\s\S]*?---\n?/, "");
}
