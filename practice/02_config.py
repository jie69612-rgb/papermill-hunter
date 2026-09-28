import os
from pathlib import Path


class Settings:
    def __init__(self):
        self.data_dir = Path(os.environ.get('PMH_DATA_DIR', 'data'))
        self.contact_email = os.environ.get('PMH_CONTACT_EMAIL', '')
        self.http_timeout = float(os.environ.get('PMH_HTTP_TIMEOUT', '30'))
        self.http_max_retries = int(os.environ.get('PMH_HTTP_MAX_RETRIES', '5'))

    @property
    def raw_dir(self):
        return self.data_dir / 'raw' # 装饰器的作用是将方法转换为属性，即可以像访问属性一样访问方法

    @property
    def interim_dir(self):
        return self.data_dir / 'interim'

    def ensure_dirs(self):
        for path in [self.raw_dir, self.interim_dir]:
            path.mkdir(parents=True, exist_ok=True)

_settings = None

def get_settings():
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

if __name__ == "__main__":
    s = get_settings()
    print('数据目录：',s.data_dir)
    print('联系邮箱：',s.contact_email or '未设置')
    print('HTTP超时时间：',s.http_timeout,'秒 类型：',type(s.http_timeout))
    print('HTTP最大重试次数：',s.http_max_retries,'类型：',type(s.http_max_retries))
    print()
    print('raw_dir:',s.raw_dir)
    print('单例验证：',get_settings() is get_settings())