# -*- coding: utf-8 -*-

from zvt.contract.recorder import Recorder
from zvt.domain.meta.cbond_meta import CBond
from zvt.recorders.em import em_api


class EMCBondRecorder(Recorder):
    provider = "em"
    data_schema = CBond

    def run(self):
        df = em_api.get_tradable_list(entity_type="cbond")
        self.logger.info(df)
        self.data_schema.df_to_db(df, provider=self.provider, force_update=self.force_update)


if __name__ == "__main__":
    recorder = EMCBondRecorder()
    recorder.run()


# the __all__ is generated
__all__ = ["EMCBondRecorder"]
