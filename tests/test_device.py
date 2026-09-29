from bot.device import dump_screen, summarize_hierarchy

XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" content-desc="" clickable="false" bounds="[0,0][1080,2400]">
    <node index="0" text="เพิ่ม" resource-id="jp.naver.line.android:id/add_button" class="android.widget.Button" content-desc="" clickable="true" bounds="[100,200][300,260]" />
    <node index="1" text="" resource-id="" class="android.widget.ImageView" content-desc="เพิ่มเพื่อน" clickable="true" bounds="[900,80][1000,160]" />
    <node index="2" text="" resource-id="" class="android.view.View" content-desc="" clickable="false" bounds="[0,0][10,10]" />
  </node>
</hierarchy>"""


def test_summarize_hierarchy_keeps_useful_elements():
    lines = summarize_hierarchy(XML).splitlines()
    assert lines == [
        "jp.naver.line.android:id/add_button | text='เพิ่ม' | desc='' | android.widget.Button | clickable | [100,200][300,260]",
        "- | text='' | desc='เพิ่มเพื่อน' | android.widget.ImageView | clickable | [900,80][1000,160]",
    ]


class FakeDevice:
    def screenshot(self, path):
        open(path, "wb").write(b"png")

    def dump_hierarchy(self):
        return XML


def test_dump_screen_writes_three_files(tmp_path):
    base = dump_screen(FakeDevice(), "result found/ผลค้นหา", out_dir=tmp_path)
    assert base.name.endswith("-result_found_ผลค้นหา")
    for suffix in (".png", ".xml", ".txt"):
        assert (tmp_path / f"{base.name}{suffix}").exists()
    assert "add_button" in (tmp_path / f"{base.name}.txt").read_text(encoding="utf-8")
