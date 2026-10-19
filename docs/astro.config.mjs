import { unified } from '@astrojs/markdown-remark'
import { defineConfig } from 'astro/config'
import starlight from '@astrojs/starlight'
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'

// ArchAI documentation site. It is built, not published: the repository is private under a
// personal GitHub account, so CI uploads `docs/dist` as a workflow artifact instead of deploying.
//
// The site is self-contained under `docs/` with its own bun toolchain, isolated from the
// Python/uv toolchain at the repository root. Images and Mermaid sources live in `docs/public/`
// and are referenced by absolute path, so the `.mmd` sources stay downloadable beside their PNGs.
//
// https://astro.build/config
export default defineConfig({
  outDir: './dist',
  markdown: {
    // The Kaggle write-up pages carry LaTeX in `$…$` and `$$…$$` delimiters; without these two
    // plugins MDX reads the braces in the formulas as JSX expressions and the build fails.
    // MDX inherits the `markdown.processor`, so this covers `.mdx` too.
    processor: unified({ remarkPlugins: [remarkMath], rehypePlugins: [rehypeKatex] }),
  },
  integrations: [
    starlight({
      title: 'ArchAI',
      description:
        'Deciphering Swiss archives with Apertus: an AI-ready, provenance-preserving dataset and a cited Q&A demo.',
      customCss: ['./src/styles/custom.css'],
      // Every page is listed explicitly: nothing is orphaned, and the order is the reading order.
      sidebar: [
        {
          label: 'Guide',
          items: [
            { label: 'Overview', link: '/' },
            { label: 'Architecture', link: '/architecture/' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'Web API', link: '/reference/api/' },
            { label: 'Record schema', link: '/reference/schema/' },
            { label: 'Rebuild from the raw data', link: '/reference/rebuild/' },
            { label: 'Outputs (data/out/)', link: '/reference/outputs/' },
            { label: 'Measured results', link: '/reference/measured-results/' },
            { label: 'Query timing', link: '/reference/query-timing/' },
            { label: 'Onboarding a new dataset', link: '/reference/onboarding/' },
            { label: 'Editing decisions (provenance)', link: '/reference/editing-decisions/' },
            { label: 'Tool agent (agent-v4)', link: '/reference/tool-agent/' },
            { label: 'Web demo and deployment', link: '/reference/web-demo/' },
            { label: 'Statistics in SQL (Apache Superset)', link: '/reference/superset/' },
            { label: 'Ethics, privacy and bias', link: '/reference/ethics/' },
          ],
        },
        {
          label: 'Diagrams',
          items: [
            { label: 'Technical architecture', link: '/diagrams/technical/' },
            { label: 'Overview', link: '/diagrams/overview/' },
          ],
        },
        {
          label: 'Kaggle write-ups',
          items: [
            { label: 'Ideas we reused', link: '/kaggle/ideas/' },
            { label: '1st place', link: '/kaggle/1st/' },
            { label: '2nd place', link: '/kaggle/2nd/' },
            { label: '3rd place', link: '/kaggle/3rd/' },
            { label: '4th place', link: '/kaggle/4th/' },
            { label: '5th place', link: '/kaggle/5th/' },
            { label: '6th place', link: '/kaggle/6th/' },
            { label: '7th place', link: '/kaggle/7th/' },
            { label: '8th place', link: '/kaggle/8th/' },
            { label: '9th place', link: '/kaggle/9th/' },
            { label: '10th place', link: '/kaggle/10th/' },
          ],
        },
      ],
    }),
  ],
})
