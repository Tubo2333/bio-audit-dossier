"""pages 包：页面模块被 demo/app.py 以 importlib 条件渲染调用。

每个页面模块提供 ``render()``；文件同时保留 Streamlit multipage 直接执行
兼容（``if __name__ == "__main__"`` 时自渲染），theme.css 已隐藏自动导航。
"""
