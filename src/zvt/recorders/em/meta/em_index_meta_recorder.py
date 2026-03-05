# -*- coding: utf-8 -*-

from zvt.contract.recorder import Recorder
from zvt.domain import Index
from zvt.recorders.em import em_api


class EMIndexRecorder(Recorder):
    provider = "em"
    data_schema = Index

    def run(self):
        df = em_api.get_tradable_list(entity_type="index", limit=100)
        self.logger.info(df)
        self.data_schema.df_to_db(df, provider=self.provider, force_update=self.force_update)


if __name__ == "__main__":
    recorder = EMIndexRecorder()
    recorder.run()


# the __all__ is generated
__all__ = ["EMIndexRecorder"]
