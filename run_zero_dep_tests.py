"""在本地模拟「没装第三方包」的环境跑测试。

CI 的「零依赖核心」作业跑的就是这个场景：核心逻辑不许依赖第三方包，
只有趋势闸门用 pandas，那组测试应该自动跳过而不是报错。

本地直接 `python -m unittest discover` 是**测不出**这条的——本机装着 pandas，
所有测试都会跑，装饰器写错了也看不出来。真出过一次：新增的测试类插进了
`@skipUnless` 和 `TrendGateTest` 之间，装饰器跟错了类，本地全绿、CI 全红。

    python run_zero_dep_tests.py
"""
import sys, unittest

BLOCKED = {"pandas", "numpy", "prophet"}

class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"blocked for test: {name}")
        return None

sys.meta_path.insert(0, Block())
for mod in [m for m in sys.modules if m.split(".")[0] in BLOCKED]:
    del sys.modules[mod]

try:
    import pandas  # noqa
    print("!! 拦截没生效，这个脚本本身坏了")
    sys.exit(2)
except ImportError:
    pass

suite = unittest.TestLoader().discover(".", pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=0).run(suite)
print(f"\n跑了 {result.testsRun} 条，失败 {len(result.failures)}，错误 {len(result.errors)}，跳过 {len(result.skipped)}")
sys.exit(0 if result.wasSuccessful() else 1)
