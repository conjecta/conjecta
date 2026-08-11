import pytest

from math_agent.tools.plot_sandbox import run_plot


@pytest.mark.asyncio
async def test_simple_plot_saved(tmp_path):
    result = await run_plot(
        "import matplotlib.pyplot as plt\n"
        "import numpy as np\n"
        "x = np.linspace(0, 6.28, 50)\n"
        "plt.plot(x, np.sin(x))\n"
        "plt.title('sin')",
        out_dir=tmp_path,
    )
    assert result.success
    assert result.figures == ["fig-1.png"]
    png = tmp_path / "fig-1.png"
    assert png.is_file()
    assert png.stat().st_size > 0
    assert png.read_bytes().startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_stdout_is_captured(tmp_path):
    result = await run_plot(
        "import matplotlib.pyplot as plt\nplt.plot([0, 1])\nprint('points:', 2)",
        out_dir=tmp_path,
    )
    assert result.success
    assert "points: 2" in result.output


@pytest.mark.asyncio
async def test_multiple_figures_saved(tmp_path):
    result = await run_plot(
        "import matplotlib.pyplot as plt\n"
        "plt.figure()\nplt.plot([0, 1])\n"
        "plt.figure()\nplt.plot([1, 0])",
        out_dir=tmp_path,
    )
    assert result.success
    assert result.figures == ["fig-1.png", "fig-2.png"]
    assert (tmp_path / "fig-1.png").is_file()
    assert (tmp_path / "fig-2.png").is_file()


@pytest.mark.asyncio
async def test_blocks_os_import(tmp_path):
    result = await run_plot("import os\nprint(os.getcwd())", out_dir=tmp_path)
    assert not result.success
    assert "Import not allowed" in result.output


@pytest.mark.asyncio
async def test_savefig_is_rejected(tmp_path):
    result = await run_plot(
        "import matplotlib.pyplot as plt\nplt.plot([1])\nplt.savefig('x.png')",
        out_dir=tmp_path,
    )
    assert not result.success
    assert "savefig" in result.output
    assert not (tmp_path / "x.png").exists()


@pytest.mark.asyncio
async def test_no_figure_is_failure(tmp_path):
    result = await run_plot("print('hello')", out_dir=tmp_path)
    assert not result.success
    assert "No figures" in result.output


@pytest.mark.asyncio
async def test_timeout_kills_runaway_code(tmp_path):
    result = await run_plot(
        "import matplotlib.pyplot as plt\nplt.plot([1])\nwhile True:\n    pass",
        out_dir=tmp_path,
        timeout=1.0,
    )
    assert not result.success
    assert result.timed_out
