import os
import re
import shutil
from dataclasses import dataclass
from typing import List, Optional

from notion2hugo.base import (
    BaseExporter,
    BaseExporterConfig,
    Blob,
    BlobType,
    ContentWithAnnotation,
    PageContent,
    register_handler,
)


def sanitize_path(name: str) -> str:
    pattern = re.compile(r"[^a-zA-Z0-9-_\.]")
    name = name.replace(" ", "_").lower()
    return pattern.sub("", name)


class MarkdownStyler:
    INC_INDENT: int = 4

    @classmethod
    def _style_content_with_annotation(cls, texts: List[ContentWithAnnotation]) -> str:
        ts = []
        for text in texts:
            t = text.plain_text
            if not t:
                return ""
            if text.bold:
                t = f"**{t}**"
            if text.italic:
                t = f"_{t}_"
            if text.strikethrough:
                t = f"~~{t}~~"
            if text.underline:
                t = f"<ins>{t}</ins>"
            if text.code:
                t = f"`{t}`"
            if text.color:
                pass
            if text.href:
                t = f"[{t}]({text.href})"
            if text.is_equation:
                t = f"$ {t} $"
            if text.highlight:
                t = f"<mark>{t}</mark>"
            ts.append(t)
        return "".join(ts)

    @classmethod
    def divider(cls, blob: Blob, indent: int) -> str:
        return "\n---\n"

    @classmethod
    def heading_1(cls, blob: Blob, indent: int) -> str:
        return f"# {cls.paragraph(blob, indent)}"

    @classmethod
    def heading_2(cls, blob: Blob, indent: int) -> str:
        return f"## {cls.paragraph(blob, indent)}"

    @classmethod
    def heading_3(cls, blob: Blob, indent: int) -> str:
        return f"### {cls.paragraph(blob, indent)}"

    @classmethod
    def equation(cls, blob: Blob, indent: int) -> str:
        return f"$$\n{cls.paragraph(blob, indent)}\n$$"

    @classmethod
    def code(cls, blob: Blob, indent: int) -> str:
        return "\n".join([f"```{blob.language}", cls.paragraph(blob, indent), "```"])

    @classmethod
    def _list_item(cls, blob: Blob, list_ch: str, indent: int) -> str:
        whitespace: str = " " * indent
        texts = [
            f"{whitespace}{list_ch} {cls._style_content_with_annotation(blob.rich_text)}"
        ]
        if blob.children:
            for child_blob in blob.children:
                texts.append(cls.process(child_blob, indent + cls.INC_INDENT))
        return "\n".join(texts)

    @classmethod
    def bulleted_list_item(cls, blob: Blob, indent: int) -> str:
        return cls._list_item(blob, list_ch="-", indent=indent)

    @classmethod
    def numbered_list_item(cls, blob: Blob, indent: int) -> str:
        return cls._list_item(blob, list_ch="1.", indent=indent)

    @classmethod
    def to_do(cls, blob: Blob, indent: int) -> str:
        return cls._list_item(
            blob, list_ch=f"- [{'X' if blob.is_checked else ' '}]", indent=indent
        )

    @classmethod
    def quote(cls, blob: Blob, indent: int) -> str:
        texts = [f"> {cls._style_content_with_annotation(blob.rich_text)}"]
        if blob.children:
            for child_blob in blob.children:
                texts.append(
                    f"> {cls._style_content_with_annotation(child_blob.rich_text)}"
                )
        return "\n>\n".join(texts)

    @classmethod
    def table(cls, blob: Blob, indent: int) -> str:
        assert blob.table_width, f"table_width expected for TABLE blob {blob}"
        rows = []
        if blob.children:
            rows = [cls.process(child_blob, indent) for child_blob in blob.children]
            if len(rows) >= 1:
                rows.insert(1, "\n|" + "---|" * blob.table_width)
        table_content = "".join(rows)
        return "\n"+'{{< bootstrap-table table_class="table table-striped table-bordered table-nonfluid w-auto" >}}'+table_content+"\n"+'{{< /bootstrap-table >}}'

    @classmethod
    def table_row(cls, blob: Blob, indent: int) -> str:
        assert blob.table_cells, f"table_cells expected for TABLE_ROW blob {blob}"
        return (
            "| "
            + " | ".join(
                cls._style_content_with_annotation(cell) for cell in blob.table_cells
            )
            + " |"
        )

    @classmethod
    def parse_caption(cls, input_string: str):
        """
        Parses the input string to extract text before and after the backslash.
        Strips leading and trailing spaces from both parts.

        Args:
            input_string (str): The input string to parse.

        Returns:
            tuple: A tuple containing the `caption` (text before the backslash)
                and `alt` (text after the backslash). If no backslash is found,
                returns None for both.
        """
        if "\\" in input_string:
            parts = input_string.split("\\", 1)  # Split only at the first backslash
            caption = parts[0].strip()
            alt = parts[1].strip()
            return caption, alt
        return input_string,""

    @classmethod
    def image(cls, blob: Blob, indent: int) -> str:
        assert blob.file and os.path.exists(
            blob.file
        ), f"file expected for IMAGE blob {blob}"
        caption = cls._style_content_with_annotation(blob.rich_text)

        ###!!!! This is super ugly
        ### this should have been this but somehow the post_images_dir is empty I dunno Why
        ### something with Async I guess so solving it like this
        # relative_path = MarkdownExporter.post_images_dir
        # Split the path into parts using '/' as the delimiter
        path_parts = blob.file.split('/')
        # Replace the first two parts with 'images'
        new_path_parts = ['images'] + path_parts[2:]
        # Join the parts back into a single path
        relative_path = '/'.join(new_path_parts)
        extract_cap, extract_alt = cls.parse_caption(caption)

        return (
            f'{{{{< img class="blog-img-center" width="800" src="{relative_path}" '
            f'caption="{extract_cap}" alt="{extract_alt}" >}}}}'
        )

    @classmethod
    def paragraph(cls, blob: Blob, indent: int) -> str:
        texts = [cls._style_content_with_annotation(blob.rich_text)]
        if blob.children:
            for child_blob in blob.children:
                texts.append(cls.process(child_blob, indent + cls.INC_INDENT))
        return "\n".join(texts)

    @classmethod
    def process(cls, blob: Optional[Blob], indent: int = 0) -> str:
        if not blob:
            return ""
        if not hasattr(cls, blob.type.value):
            raise ValueError(
                f"{cls.__qualname__} does not support blob type = {blob.type.value}.\n"
                f"Blob: {blob}"
            )
        return "\n" + getattr(cls, blob.type.value)(blob, indent)

    @classmethod
    def video(cls, blob: Blob, indent: int) -> str:
       title = cls._style_content_with_annotation(blob.rich_text)
       assert title!="", "Missing Title for the youtube video, add caption"
       assert blob.url.startswith("https://youtu.be/")
       video_id = blob.url[len("https://youtu.be/"):]
       return f'{{{{< youtube id="{video_id}" title="{title}" width=60 >}}}}'


    @classmethod
    def column_list(cls, blob: Blob, indent: int) -> str:
        texts = [cls._style_content_with_annotation(blob.rich_text)]
        if blob.children:
            for child_blob in blob.children:
                texts.append(cls.process(child_blob, indent + cls.INC_INDENT))
        return "\n".join(texts)

    @classmethod
    def column(cls, blob: Blob, indent: int) -> str:
        return cls._list_item(
            blob, list_ch=f"-column", indent=indent
        )

    @classmethod
    def callout(cls, blob: Blob, indent: int) -> str:
        text =  cls._style_content_with_annotation(blob.rich_text)
        return f'{{{{< callout emoji="" text="{text}" >}}}}'
@dataclass(frozen=True)
class MarkdownExporterConfig(BaseExporterConfig):
    parent_dir: str
    # use one of the page properties to determine post dir/file name
    # # if not specified, we default to using "id" as name
    post_name_property_key: Optional[str] = None


@register_handler(MarkdownExporterConfig)
class MarkdownExporter(BaseExporter):
    POST_FILE_NAME: str = "index.md"
    POST_IMAGES_DIR: str = "images/"
    post_images_dir = ""

    def __init__(self, config: MarkdownExporterConfig):
        super(MarkdownExporter, self).__init__(config)
        self.config: MarkdownExporterConfig = config
        self.logger.info(f"Clean up parent dir: {self.config.parent_dir}")
        self.cleanup_parent_dir(self.config.parent_dir)

    def cleanup_parent_dir(self, parent_dir: str) -> None:
        if os.path.exists(parent_dir):
            shutil.rmtree(parent_dir)

    def remove_line_with_caption(self, lines, keyword):
        return [line for line in lines if keyword not in line]


    def make_output_dirs(self, parent_dir: str, *args: str) -> None:
        os.makedirs(os.path.join(parent_dir, *args), exist_ok=True)

    async def async_process(self, content: PageContent) -> None:
        # prepare post output dir structure
        # parent_dir/
        #     post_1/
        #         images/
        #         index.md
        assert not self.config.post_name_property_key or content.properties.get(
            self.config.post_name_property_key
        ), (
            f"{self.config.post_name_property_key} not a valid "
            "property [{content.properties.keys()}]"
        )
        post_dir_name = (
            content.properties[self.config.post_name_property_key]
            if self.config.post_name_property_key
            else content.id
        )

        assert isinstance(post_dir_name, str), f"{post_dir_name} expected to be str"
        post_dir_name = sanitize_path(post_dir_name)
        self.post_images_dir = os.path.join(
            self.config.parent_dir, post_dir_name, f"{post_dir_name}_{self.POST_IMAGES_DIR}"
        )
        self.logger.debug(f"Creating output dir structure: {self.post_images_dir}")
        self.make_output_dirs(self.post_images_dir)
        post_full_path = os.path.join(
            self.config.parent_dir, post_dir_name, f"{post_dir_name}.md"
        )

        # prepare post content and write it out
        self.logger.debug("Processing blobs to prepare markdown content")
        texts = []
        texts.append(MarkdownStyler.process(content.header))
        for blob in content.blobs:
            if blob.type == BlobType.IMAGE or \
               blob.type == BlobType.PARAGRAPH or \
               blob.type == BlobType.NUMBERED_LIST_ITEM or \
               blob.type == BlobType.BULLETED_LIST_ITEM:
                    #TODO ATTI Nested Items Debug
                # if blob.type == BlobType.NUMBERED_LIST_ITEM:
                    # from IPython import embed
                    # import nest_asyncio
                    # nest_asyncio.apply()
                    # embed(using='asyncio')
                if  blob.file and os.path.exists( blob.file):
                    new_img_path = shutil.copy(blob.file, self.post_images_dir)
                    self.logger.info(f"Copy file {blob.file}")
                    blob = Blob(
                        id=blob.id,
                        rich_text=blob.rich_text,
                        type=blob.type,
                        children=blob.children,
                        file=new_img_path,
                        language=blob.language,
                        table_width=blob.table_width,
                        table_cells=blob.table_cells,
                        is_checked=blob.is_checked,
                        url = blob.url
                    )
            texts.append(MarkdownStyler.process(blob))
        # Remove the duplicated featureimage from the body
        texts = self.remove_line_with_caption(texts, 'caption="featureimage"')
        texts.append(MarkdownStyler.process(content.footer))

        self.logger.info(f"Export post id={content.id} to path='{post_full_path}'")
        with open(post_full_path, "w") as fp:
            fp.write("\n".join(texts).strip())


        # TODO change the constants
        target_path = f"../content/blog-entries/{os.path.basename(post_full_path)}"
        # ask the user for confirmation
        response = input(f"do you want to copy {post_full_path} to {target_path}? (y/n): ").strip().lower()
        if response == "y":
            try:
                # copy the file to the blog-entries
                shutil.copy(post_full_path, target_path)
                self.logger.warning(f"File copied to {target_path}")
                shutil.copytree(self.post_images_dir,
                                f"../assets/images/{os.path.basename(self.post_images_dir.rstrip('/')).lower()}",
                                dirs_exist_ok=True)
            except exception as e:
                self.logger.error(f"error copying file: {e}")

