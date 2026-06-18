import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import { basename, dirname, isAbsolute, join, resolve } from "path";

type RoadToCodeSection = {
  heading: string;
  level: 2 | 3;
  paragraphs: string[];
  bullet_points?: string[];
};

type RoadToCodePayload = {
  schema_version: string;
  event_id: string;
  content: {
    title: string;
    summary: string;
    sections: RoadToCodeSection[];
  };
  assets: {
    images: Array<{
      asset_ref: string;
      caption?: string;
      alt?: string;
    }>;
  };
  meta: {
    date: string;
    language: string;
    tags: string[];
  };
  targets?: {
    astro?: {
      collection?: string;
      slug?: string;
      draft?: boolean;
    };
  };
};

type CliArgs = {
  input: string;
  contentDir: string;
  publicDir: string;
  assetsDir: string;
};

function parseArgs(argv: string[]): CliArgs {
  const args = [...argv];

  const getOption = (name: string, fallback: string): string => {
    const index = args.indexOf(name);
    if (index >= 0 && args[index + 1]) {
      return args[index + 1];
    }
    return fallback;
  };

  const input = getOption("--input", "");
  if (!input) {
    throw new Error("Missing --input <payload.json>");
  }

  return {
    input,
    contentDir: getOption("--content-dir", "./content"),
    publicDir: getOption("--public-dir", "./public/images/roadtocode"),
    assetsDir: getOption("--assets-dir", "."),
  };
}

function ensureDirectory(pathname: string): void {
  if (!existsSync(pathname)) {
    mkdirSync(pathname, { recursive: true });
  }
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-");
}

function resolveAssetLocalPath(assetRef: string, assetsDir: string): string | null {
  if (!assetRef || /^https?:\/\//i.test(assetRef) || assetRef.startsWith("telegram://")) {
    return null;
  }

  const normalized = assetRef.startsWith("file://") ? assetRef.replace("file://", "") : assetRef;
  const absolute = isAbsolute(normalized) ? normalized : resolve(assetsDir, normalized);

  if (!existsSync(absolute)) {
    return null;
  }

  return absolute;
}

function syncImages(payload: RoadToCodePayload, publicDir: string, assetsDir: string): string[] {
  ensureDirectory(publicDir);

  const targetRefs: string[] = [];
  for (const image of payload.assets.images) {
    const sourcePath = resolveAssetLocalPath(image.asset_ref, assetsDir);
    if (!sourcePath) {
      continue;
    }

    const targetName = basename(sourcePath);
    const targetPath = join(publicDir, targetName);
    copyFileSync(sourcePath, targetPath);
    targetRefs.push(targetPath);
  }

  return targetRefs;
}

function renderMarkdown(payload: RoadToCodePayload): string {
  const collection = payload.targets?.astro?.collection ?? "blog";
  const slug = payload.targets?.astro?.slug ?? slugify(payload.content.title);
  const draft = payload.targets?.astro?.draft ?? true;

  const frontmatter = [
    "---",
    `title: ${JSON.stringify(payload.content.title)}`,
    `description: ${JSON.stringify(payload.content.summary)}`,
    `date: ${JSON.stringify(payload.meta.date)}`,
    `language: ${JSON.stringify(payload.meta.language)}`,
    `tags: ${JSON.stringify(payload.meta.tags)}`,
    `draft: ${JSON.stringify(draft)}`,
    `slug: ${JSON.stringify(slug)}`,
    `collection: ${JSON.stringify(collection)}`,
    "---",
    "",
  ].join("\n");

  const body: string[] = [];

  for (const section of payload.content.sections) {
    const headingPrefix = section.level === 3 ? "###" : "##";
    body.push(`${headingPrefix} ${section.heading}`);
    body.push("");

    for (const paragraph of section.paragraphs) {
      body.push(paragraph);
      body.push("");
    }

    for (const bullet of section.bullet_points ?? []) {
      body.push(`- ${bullet}`);
    }

    if ((section.bullet_points ?? []).length > 0) {
      body.push("");
    }
  }

  return frontmatter + body.join("\n");
}

if (require.main === module) {
  let cli: CliArgs;
  try {
    cli = parseArgs(process.argv.slice(2));
  } catch (error) {
    process.stderr.write(`Error: ${(error as Error).message}\n`);
    process.stderr.write(
      "Usage: node dist/index.js --input payload.json [--content-dir ./content] [--public-dir ./public/images/roadtocode] [--assets-dir .]\n"
    );
    process.exit(1);
  }

  const rawPayload = readFileSync(resolve(cli.input), "utf-8");
  const payload = JSON.parse(rawPayload) as RoadToCodePayload;
  const collection = payload.targets?.astro?.collection ?? "blog";
  const slug = payload.targets?.astro?.slug ?? slugify(payload.content.title);

  const targetDir = resolve(cli.contentDir, collection);
  ensureDirectory(targetDir);

  syncImages(payload, resolve(cli.publicDir), resolve(cli.assetsDir));

  const markdown = renderMarkdown(payload);
  const outputPath = resolve(targetDir, `${slug}.md`);
  ensureDirectory(dirname(outputPath));
  writeFileSync(outputPath, markdown + "\n", "utf-8");

  process.stdout.write(
    JSON.stringify(
      {
        status: "ok",
        event_id: payload.event_id,
        output_path: outputPath,
      },
      null,
      2
    ) + "\n"
  );
}

export { renderMarkdown };
