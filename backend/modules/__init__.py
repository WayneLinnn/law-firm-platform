"""业务模块。每个子包 = 一个可独立装卸的模块。

约定的文件结构（无一例外）：
    modules/<name>/
        manifest.py   声明依赖、权限点          【必须】
        api.py        对外唯一出口              【必须】
        service.py    业务逻辑
        models.py     ORM，独占 schema
        router.py     HTTP 路由
        migrations/versions/   本模块自带的迁移
"""
